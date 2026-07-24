#!/usr/bin/env python3
"""
Claude Session Browser  (pywebview-Edition)
===========================================
Modernes natives Fenster (HTML/CSS/JS-Oberflaeche, Python-Backend) zum
Durchsuchen aller lokalen Claude-Code-Sessions und Wiedereinstieg per Klick.

Start:   python claude_sessions.py
Bauen:   pyinstaller --onefile --noconsole --name ClaudeSessionBrowser \
                      --icon claude_sessions.ico \
                      --add-data "logo.png;." claude_sessions.py
"""

import os
import re
import sys
import ssl
import json
import time
import zlib
import queue
import shutil
import base64
import ctypes
import logging
import tempfile
import threading
import webbrowser
import datetime as dt
import subprocess
import urllib.request

import webview

# Nur damit PyInstaller die Tcl/Tk-Daten mit-buendelt (der eigentliche Import
# passiert lazy im BuddyController-Thread).
try:
    import tkinter as _tk_probe  # noqa: F401
    import _tkinter as _tkc_probe  # noqa: F401
except Exception:
    pass

# pywebview-Introspektions-Geschwaetz daempfen (harmlose COM-/Rekursionswarnungen)
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

# ----- Version & Update ---------------------------------------------------- #
VERSION = "1.0.18"
# Wird beim GitHub-Setup auf dein echtes Repo gesetzt (OWNER/REPO):
UPDATE_URL = "https://raw.githubusercontent.com/juppeee/claude-session-browser/main/version.json"


def _vtuple(v):
    out = []
    for p in str(v).lstrip("vV").split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


# --------------------------------------------------------------------------- #
#  Pfade / Hilfen
# --------------------------------------------------------------------------- #
HOME = os.path.expanduser("~")
TITLES_FILE = os.path.join(HOME, ".claude", "session_titles.json")
SETTINGS_FILE = os.path.join(HOME, ".claude", "session_browser_settings.json")


def _resource(name):
    """Pfad zu mitgelieferter Datei – als .py (neben dem Script) und als
    gebaute .exe (PyInstaller entpackt nach sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def norm(p):
    return os.path.normcase(os.path.normpath(p)) if p else ""


DEFAULT_SETTINGS = {
    "hide_home": False,
    "hidden_folders": [],
    "session_colors": {},
    "sort_col": "when",
    "sort_rev": True,
    "projects_dir": "",          # leer = automatisch suchen
    "accent": "#ec7456",         # Akzentfarbe der Oberflaeche (Koralle, passend zum Logo)
    "bg_base": "#4a3a30",        # Grundton -> daraus wird die Hintergrund-Palette abgeleitet (warm)
    "terminal": "auto",          # auto | wt | cmd
    "claude_cmd": "claude",      # Befehl/Pfad zur Claude-CLI
    "columns": [                 # sichtbare Spalten + Reihenfolge
        {"key": "title", "on": True}, {"key": "project", "on": True},
        {"key": "msgs", "on": True}, {"key": "when", "on": True},
        {"key": "id", "on": False}, {"key": "first", "on": False},
    ],
    "win_w": 0, "win_h": 0,      # gemerkte Fenstergroesse (0 = noch nicht gesetzt)
    "win_x": None, "win_y": None,  # gemerkte Position
    "win_max": False,            # war das Fenster maximiert?
    "close_to_tray": True,       # X = App verstecken (Tray-Icon) statt beenden
    "autostart": True,           # Beim Windows-Start automatisch mitstarten
    "autostart_registered": False,  # Merker: Registry-Eintrag beim ersten Mal setzen
    "notify_limit_reset": True,  # Windows-Notification wenn Claude-Limit sich zurueckgesetzt hat
    "onboarded": False,          # Erst-Einrichtung schon durchlaufen?
    "onboarded_version": "",     # zuletzt gesehene Onboarding-Version (fuer Re-Onboarding nach Updates)
    "buddy": {                   # Clawd-Buddy: kleines animiertes Desktop-Maskottchen
        "enabled": False,
        "size": 4,               # Skalierung: 20 px * size -> tatsaechliche Kantenlaenge
        "visibility": "when_claude",  # "when_claude" | "always" | "when_window"
        "target_window": "",     # Titel-Substring (Kleinschreibung) fuer visibility=when_window
        "x": 200, "y": 200,      # gemerkte Position auf dem Desktop
        "opacity": 100,          # 20..100 (Prozent) – 100 = voll deckend
        "party": False,          # Party-Modus: nur Tanz-Animation
        "frame": False,          # (legacy) duenner Rahmen um den Buddy
        "frame_style": "off",    # "off" | "line" | "webcam"
        "frame_color": "#ec7456",
        "frame_label": "CLAWD",
    },
}

# Wenn diese Konstante sich aendert, sehen bestehende Nutzer das Onboarding erneut
# (ohne dass ihre Einstellungen ueberschrieben werden – die Schritte zeigen die
# aktuellen Werte an, ein Klick auf "Weiter" ohne Aenderung laesst alles wie es ist).
ONBOARDING_VERSION = "1.0.12"

# --------------------------------------------------------------------------- #
#  Buddy (Clawd-Maskottchen) – Sprite-Daten + Steuerung
# --------------------------------------------------------------------------- #
# Komprimierte 20x20-Pixel-Sprites aus dem Clawdmeter-Projekt
# (zlib + base64). 14 Animationen, entpackt ~192 KB. Ausgelagert in ein
# eigenes Modul, weil der Blob 3 KB gross ist.
try:
    from clawd_sprites import BLOB as BUDDY_BLOB
except Exception:
    BUDDY_BLOB = ""

def _decode_buddy_anims():
    """Entpackt die eingebetteten Sprites zu einer Dict-Struktur:
       {name: {"palette": [10 hex-Farben], "frames": [[400 ints], ...]}}"""
    try:
        raw = zlib.decompress(base64.b64decode(BUDDY_BLOB))
        arr = json.loads(raw.decode("utf-8"))
        return {a["n"]: {"palette": a["p"], "frames": a["f"]} for a in arr}
    except Exception:
        return {}


BUDDY_ANIMS = _decode_buddy_anims()

# Mapping von "detektiertem Zustand" -> Animations-Name.
BUDDY_STATE_MAP = {
    "limit":    "limit",             # Rate/Usage-Limit erreicht (sauer!)
    "active":   "work coding",       # Claude schreibt gerade (mtime < 3 s)
    "thinking": "work think",        # kurz danach, wenn's noch zappelt
    "recent":   "idle blink",        # zwischendurch mal blinzeln
    "idle":     "idle breathe",      # entspannt atmen
    "sleep":    "expression sleep",  # lange nichts los -> schlaeft
    "none":     "idle look around",  # kein Claude installiert / kein Projekt
    "party":    "dance bounce",      # Party-Modus
    "surprise": "expression surprise",
}

# Sehr spezifische Muster in der neuesten .jsonl-Datei die eindeutig auf ein
# erreichtes Claude-Nutzungslimit hindeuten. Absichtlich streng gewaehlt
# damit normale Chat-Erwaehnungen von „rate limit" o.ae. NICHT triggern.
_LIMIT_PATTERNS = re.compile(
    # Klare "erreicht"-Phrasen (5h / weekly / session / max)
    r"(?:you'?ve reached your (?:5.?hour|weekly|daily|24.?hour|max|session) limit)"
    # Explizites "reached"
    r"|(?:(?:5.?hour|weekly|daily|24.?hour|session|usage) limit reached)"
    # "session limit · resets ..." wie in der Claude-CLI-Statuszeile
    r"|(?:session limit[^\n]{0,40}resets?)"
    # Ganz nah dran (>= 90% verbraucht) – triggert auch die Vorwarn-Phase
    r"|(?:used 9\d%[^\n]{0,20}session limit)"
    r"|(?:used 100%[^\n]{0,20}session limit)"
    # Claude Max Plan Limit
    r"|(?:claude max plan[^\n]{0,80}limit reached)"
    # API-Fehler die Claude oft bei Ueberlast/Rate-Limit wirft
    r"|(?:api error[^\n]{0,40}server error mid.?response)"
    r"|(?:api error[^\n]{0,40}overloaded)"
    r"|(?:\"type\":\s*\"overloaded_error\")"
    r"|(?:rate_limit_error)"
    r"|(?:429[^\n]{0,20}too many requests)"
    # Auth-Fehler (Token abgelaufen/widerrufen) – User kann nicht arbeiten
    r"|(?:please run /login)"
    r"|(?:401[^\n]{0,60}(?:oauth|access token|unauthori[sz]ed))"
    r"|(?:access token has been revoked)"
    r"|(?:\"type\":\s*\"authentication_error\")"
    r"|(?:invalid[_ ]api[_ ]key)"
    r"|(?:authentication[^\n]{0,20}failed)",
    re.IGNORECASE,
)


def _latest_jsonl_hits_limit(projects_dir, max_files=200, tail_kb=6):
    """Liest die neuesten Zeilen der zuletzt geaenderten .jsonl-Datei und
    prueft ob dort ein Limit-Hinweis steht. Rueckgabe: True/False. Wird nur
    aufgerufen wenn ohnehin frische Aktivitaet erkannt wurde – bleibt billig."""
    if not projects_dir or not os.path.isdir(projects_dir):
        return False
    newest_path = None
    newest_mtime = 0.0
    count = 0
    try:
        for entry in os.scandir(projects_dir):
            if not entry.is_dir():
                continue
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_file() and sub.name.endswith(".jsonl"):
                        try:
                            m = sub.stat().st_mtime
                            if m > newest_mtime:
                                newest_mtime = m
                                newest_path = sub.path
                        except OSError:
                            pass
                        count += 1
                        if count >= max_files:
                            break
            except OSError:
                pass
            if count >= max_files:
                break
    except OSError:
        pass
    if not newest_path:
        return False
    # Session-JSONL zeilenweise parsen und STRUKTURELL nach echten Fehler-
    # Markern suchen. Reine Text-Erwaehnungen von "authentication_error" o.ae.
    # in normalen Chat-Nachrichten sollen nicht triggern – nur Fehler die
    # tatsaechlich Feld-basiert markiert sind.
    try:
        with open(newest_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            # Wir brauchen ganze Zeilen – ggf. bis Zeilenanfang zurueck-suchen.
            start = max(0, size - tail_kb * 1024)
            fh.seek(start)
            if start > 0:
                fh.readline()  # unvollstaendige erste Zeile ueberspringen
            chunk = fh.read()
        text = chunk.decode("utf-8", errors="replace")
    except OSError:
        return False

    def _line_is_real_error(obj):
        """True nur wenn diese JSONL-Zeile STRUKTURELL einen echten Fehler
        signalisiert – nicht wenn 'error' bloss als Text darin steht."""
        if not isinstance(obj, dict):
            return False
        # 1) top-level: {"type":"error", ...} oder isError im Aussenrahmen
        typ = obj.get("type")
        if typ in ("error", "tool_use_error", "system_error"):
            return True
        if obj.get("isError") is True or obj.get("is_error") is True:
            return True
        # 2) message.stop_reason zeigt Fehler an
        msg = obj.get("message")
        if isinstance(msg, dict):
            sr = msg.get("stop_reason")
            if sr in ("error", "rate_limited", "overloaded",
                      "authentication_error"):
                return True
            # 3) content-Items mit is_error / tool_use_error
            content = msg.get("content")
            if isinstance(content, list):
                for it in content:
                    if not isinstance(it, dict):
                        continue
                    if it.get("is_error") is True or it.get("isError") is True:
                        return True
                    if it.get("type") in ("tool_use_error", "error"):
                        return True
        # 4) top-level "error"-Objekt vorhanden
        err = obj.get("error")
        if isinstance(err, dict) and err.get("type"):
            return True
        return False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not _line_is_real_error(obj):
            continue
        if _LIMIT_PATTERNS.search(line):
            return True
    return False


def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return fallback


_SAVE_LOCK = threading.Lock()


def save_json(path, data):
    """Atomischer Write: erst in .tmp schreiben, dann os.replace() (atomar
    unter Windows). Verhindert dass zwei Threads gleichzeitig eine kaputte
    Datei hinterlassen."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return
    tmp = path + ".tmp"
    with _SAVE_LOCK:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    # verschachtelte Defaults muessen als Kopie in `data` — sonst teilen sich
    # alle Instanzen dieselbe Referenz.
    data["buddy"] = dict(DEFAULT_SETTINGS["buddy"])
    raw = load_json(SETTINGS_FILE, None)
    if raw:
        raw_buddy = raw.get("buddy") if isinstance(raw.get("buddy"), dict) else None
        data.update(raw)
        if raw_buddy:
            merged = dict(DEFAULT_SETTINGS["buddy"])
            merged.update(raw_buddy)
            data["buddy"] = merged
        # Bestandsnutzer (Datei existiert) sehen kein Onboarding,
        # ausser der Schluessel ist bereits gesetzt.
        if "onboarded" not in raw:
            data["onboarded"] = True
    else:
        data["onboarded"] = False   # echte Erstinstallation
    return data


# --------------------------------------------------------------------------- #
#  Sessions-Ordner finden
# --------------------------------------------------------------------------- #
def detect_projects_dir():
    """Sucht den Claude-Projektordner an gaengigen Orten."""
    candidates = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        candidates.append(os.path.join(env, "projects"))
    candidates += [
        os.path.join(HOME, ".claude", "projects"),
        os.path.join(HOME, ".config", "claude", "projects"),
        os.path.join(os.environ.get("APPDATA", ""), "claude", "projects"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "claude", "projects"),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return ""


# --------------------------------------------------------------------------- #
#  Parsing der .jsonl Session-Dateien
# --------------------------------------------------------------------------- #
def _first_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
    return ""


def clean_user_text(t):
    """Entfernt System-/Befehls-Wrapper (z. B. <local-command-caveat>,
    <command-name>, <system-reminder>), damit die echte erste Frage uebrig bleibt."""
    if not t:
        return ""
    # bekannte Bloecke komplett entfernen (auch mehrzeilig)
    for tag in ("local-command-caveat", "local-command-stdout", "system-reminder",
                "command-name", "command-message", "command-args", "command-contents"):
        t = re.sub(r"<%s>.*?</%s>" % (tag, tag), " ", t, flags=re.S | re.I)
        t = re.sub(r"</?%s>" % tag, " ", t, flags=re.I)  # auch unvollstaendige
    # die typische Caveat-Warnung als Klartext entfernen, falls ohne Tags
    t = re.sub(r"Caveat:.*?unless the user explicitly asks.*?\.", " ", t, flags=re.S | re.I)
    # restliche spitzklammer-Tags raus
    t = re.sub(r"</?[a-zA-Z][\w-]*>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_session(path):
    session_id = os.path.splitext(os.path.basename(path))[0]
    ai_title = first_user = cwd = last_ts = None
    user_msgs = assistant_msgs = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue   # valides JSON, aber kein Objekt -> ueberspringen
                t = d.get("type")
                if d.get("cwd") and cwd is None:
                    cwd = d["cwd"]   # ERSTES cwd = Start-Verzeichnis (passt zum
                    #                  Projektordner; 'claude --resume' findet die
                    #                  Session nur dort, nicht in spaeteren Unterordnern)
                if d.get("timestamp"):
                    last_ts = d["timestamp"]
                if t == "ai-title":
                    ai_title = d.get("aiTitle") or ai_title
                elif t == "user":
                    user_msgs += 1
                    if first_user is None:
                        txt = clean_user_text(_first_text(d.get("message", {}).get("content")))
                        if txt:
                            first_user = txt
                elif t == "assistant":
                    assistant_msgs += 1
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if user_msgs == 0 and assistant_msgs == 0 and not ai_title:
        return None
    auto_title = ai_title or (first_user[:90] if first_user else "(ohne Titel)")
    return {
        "id": session_id,
        "auto_title": auto_title,
        "first_user": first_user or "",
        "cwd": cwd or "",
        "mtime": mtime,
        "user_msgs": user_msgs,
        "assistant_msgs": assistant_msgs,
        "total_msgs": user_msgs + assistant_msgs,
    }


def collect_sessions(projects_dir):
    out = []
    if not projects_dir or not os.path.isdir(projects_dir):
        return out
    for project in os.listdir(projects_dir):
        pdir = os.path.join(projects_dir, project)
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            if not name.endswith(".jsonl"):
                continue
            info = parse_session(os.path.join(pdir, name))
            if info:
                info["project"] = project
                out.append(info)
    return out


def fmt_time(mtime):
    d = dt.datetime.fromtimestamp(mtime)
    today = dt.date.today()
    diff = (today - d.date()).days
    if diff == 0:
        return "heute " + d.strftime("%H:%M")
    if diff == 1:
        return "gestern " + d.strftime("%H:%M")
    if diff < 7:
        return f"vor {diff} Tagen"
    return d.strftime("%d.%m.%Y")


# --------------------------------------------------------------------------- #
#  Resume
# --------------------------------------------------------------------------- #
def decode_project(folder):
    """Rekonstruiert das Verzeichnis aus dem Projektordner-Namen.
    Achtung: verlustbehaftet (ein '-' im echten Ordnernamen ist nicht von einem
    Pfadtrenner unterscheidbar) -> nur als Notfall-Fallback verwenden."""
    if not folder:
        return ""
    return folder.replace("--", ":\\", 1).replace("-", "\\")


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe_workdir(cwd, project=""):
    if cwd and os.path.isdir(cwd):
        return cwd
    dec = decode_project(project)
    return dec if dec and os.path.isdir(dec) else HOME


def resume_session(session_id, cwd, settings, project=""):
    # Session-ID hart validieren – sie fliesst in eine Terminal-Kommandozeile.
    # Ein bloedes Zeichen (& | " `) waere sonst Command-Injection.
    sid = str(session_id or "")
    if not _SESSION_ID_RE.match(sid):
        return {"ok": False, "error": "Ungültige Session-ID."}
    workdir = _safe_workdir(cwd, project)
    claude = settings.get("claude_cmd") or "claude"
    # `claude_cmd` kommt aus User-Settings – wir erlauben nur einen einfachen
    # Programmnamen oder absoluten Pfad, keine Shell-Metazeichen.
    if any(c in claude for c in '&|;<>"`$'):
        return {"ok": False, "error": "Unsicherer claude_cmd-Wert."}
    term = settings.get("terminal", "auto")
    try:
        if term in ("auto", "wt"):
            try:
                # argv-Form, KEIN shell=True -> keine Shell-Interpretation
                subprocess.Popen(["wt", "-d", workdir, "cmd", "/k",
                                  claude, "--resume", sid])
                return {"ok": True}
            except FileNotFoundError:
                if term == "wt":
                    return {"ok": False, "error": "Windows Terminal (wt) nicht gefunden."}
        # Fallback: cmd.exe direkt starten – ohne shell=True, argv als Liste.
        # `start` ist ein cmd-Builtin, deshalb rufen wir cmd /c start …
        subprocess.Popen(
            ["cmd", "/c", "start", "Claude Code", "/D", workdir,
             "cmd", "/k", claude, "--resume", sid])
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
#  Buddy-Controller (Tkinter-Fenster in eigenem Daemon-Thread)
# --------------------------------------------------------------------------- #
_IS_WIN = sys.platform == "win32"


def _win_enum_monitors():
    """Liste aller Monitore mit Arbeitsbereich, primaer-Flag, Kurzlabel.
    Rueckgabe: [{'idx': 0, 'left': ..., 'top': ..., 'right': ..., 'bottom': ...,
    'primary': True/False, 'label': 'Primär 1920×1080'}]"""
    if not _IS_WIN:
        return []

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong)]

    u = ctypes.windll.user32
    result = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_void_p)

    def cb(hmon, _hdc, _lprect, _lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            result.append({
                "left": mi.rcWork.l, "top": mi.rcWork.t,
                "right": mi.rcWork.r, "bottom": mi.rcWork.b,
                "primary": bool(mi.dwFlags & 1),
            })
        return True

    try:
        u.EnumDisplayMonitors(0, 0, MONITORENUMPROC(cb), 0)
    except Exception:
        return []

    # Primaeren nach vorne, Rest links->rechts oben->unten
    result.sort(key=lambda m: (0 if m["primary"] else 1, m["top"], m["left"]))
    for i, m in enumerate(result):
        w = m["right"] - m["left"]
        h = m["bottom"] - m["top"]
        tag = "Primär" if m["primary"] else f"Monitor {i+1}"
        m["idx"] = i
        m["label"] = f"{tag} · {w}×{h}"
    return result


def _win_monitor_work_from_point(x, y):
    """Arbeitsbereich (ohne Taskleiste) des Monitors, auf dem Punkt (x,y)
    liegt. Rueckgabe: (left, top, right, bottom) oder None."""
    if not _IS_WIN:
        return None
    try:
        u = ctypes.windll.user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", ctypes.c_ulong)]

        MonitorFromPoint = u.MonitorFromPoint
        MonitorFromPoint.restype = ctypes.c_void_p
        MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
        hmon = MonitorFromPoint(POINT(int(x), int(y)), 2)  # NEAREST
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        GetMonitorInfoW = u.GetMonitorInfoW
        GetMonitorInfoW.restype = ctypes.c_bool
        GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return (mi.rcWork.l, mi.rcWork.t, mi.rcWork.r, mi.rcWork.b)
    except Exception:
        pass
    return None


def _snap_position(x, y, size_px, grid=8, edge=32):
    """Rastert (x,y) auf ein feines Raster und schnappt an Bildschirmraender.
    `size_px` ist die Kantenlaenge des Buddy-Fensters. Rueckgabe: (nx, ny)."""
    # Feines Raster (8 px) – rundet auf naechsten Rasterpunkt statt abzuschneiden
    def _snap(v, g):
        return int(round(v / g)) * g
    nx = _snap(x, grid)
    ny = _snap(y, grid)
    # Bildschirmrand-Snap (dominiert das Feinraster wenn nah dran)
    rect = _win_monitor_work_from_point(x + size_px // 2, y + size_px // 2)
    if rect:
        l, t, r, b = rect
        if abs(nx - l) < edge:
            nx = l
        elif abs((nx + size_px) - r) < edge:
            nx = r - size_px
        if abs(ny - t) < edge:
            ny = t
        elif abs((ny + size_px) - b) < edge:
            ny = b - size_px
    return int(nx), int(ny)


def _anchor_position(anchor, size_px, current_x, current_y, monitor_idx=None):
    """Springt zu einem benannten Ankerpunkt eines Monitors. anchor: tl,tc,tr,
    ml,c,mr,bl,bc,br. Wenn `monitor_idx` gesetzt ist, wird der Monitor aus
    `_win_enum_monitors()` gewaehlt; sonst der aktuelle Monitor unterm Buddy."""
    rect = None
    if monitor_idx is not None:
        mons = _win_enum_monitors()
        if 0 <= monitor_idx < len(mons):
            m = mons[monitor_idx]
            rect = (m["left"], m["top"], m["right"], m["bottom"])
    if rect is None:
        rect = _win_monitor_work_from_point(current_x + size_px // 2,
                                            current_y + size_px // 2)
    if not rect:
        return current_x, current_y
    l, t, r, b = rect
    m = 16
    xmid = (l + r - size_px) // 2
    ymid = (t + b - size_px) // 2
    pos = {
        "tl": (l + m, t + m),
        "tc": (xmid, t + m),
        "tr": (r - size_px - m, t + m),
        "ml": (l + m, ymid),
        "c":  (xmid, ymid),
        "mr": (r - size_px - m, ymid),
        "bl": (l + m, b - size_px - m),
        "bc": (xmid, b - size_px - m),
        "br": (r - size_px - m, b - size_px - m),
    }
    return pos.get(anchor, (current_x, current_y))


def _win_foreground_title():
    """Titel des aktuell fokussierten Fensters (nur Windows). Leerer String
    wenn nicht ermittelbar."""
    if not _IS_WIN:
        return ""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return ""
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or ""
    except Exception:
        return ""


def _win_process_names():
    """Liste aller aktuell laufenden Prozessnamen (lowercase). Nutzt die
    Toolhelp-Snapshot-API von Windows."""
    if not _IS_WIN:
        return []
    try:
        from ctypes import wintypes
        TH32CS_SNAPPROCESS = 0x2

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k = ctypes.windll.kernel32
        h = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h in (0, -1):
            return []
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        names = []
        if k.Process32First(h, ctypes.byref(pe)):
            while True:
                names.append(pe.szExeFile.decode("utf-8", errors="replace").lower())
                if not k.Process32Next(h, ctypes.byref(pe)):
                    break
        k.CloseHandle(h)
        return names
    except Exception:
        return []


# Cache fuer den Prozess-/Fenster-Scan – wird alle 2 s neu berechnet.
_CLAUDE_CACHE = {"t": 0.0, "active": False}
# Genauer Titel unseres Hauptfensters (pywebview.create_window). Wichtig:
# EXAKT-Match nutzen – nicht als Substring – damit z.B. das Claude-Code-CLI
# Fenster mit Titel „⠂ Claude Session Browser Tool Development" nicht
# faelschlich als eigener Fenster aussortiert wird.
_OWN_APP_TITLE_EXACT = "claude session browser"


def _claude_context_active():
    """True wenn ein echtes Claude-CLI-Terminal offen (oder sehr kuerzlich
    aktiv) ist. Robuste Kombination:
      1) Prozess 'claude.exe' laeuft, ODER
      2) irgendein sichtbares Fenster hat 'claude' im Titel und ist nicht
         der Session Browser und nicht offensichtlich ein Browser-Tab, ODER
      3) irgendeine Session-.jsonl-Datei wurde in den letzten 5 min veraendert
         (Claude ist frisch aktiv, selbst wenn Prozess/Fenster nicht erkennbar).
    Cache 2 s."""
    now = time.time()
    if now - _CLAUDE_CACHE["t"] < 2.0:
        return _CLAUDE_CACHE["active"]
    _CLAUDE_CACHE["t"] = now
    active = False
    try:
        # 1) claude.exe direkt (native Installation)
        for n in _win_process_names():
            if n == "claude.exe":
                active = True
                break
        # 2) Fenster mit 'claude' im Titel (locker), Browser + Eigen-App raus
        if not active:
            browser_hints = (
                " — google chrome", " - google chrome",
                " — firefox", " - firefox",
                " — brave", " - brave",
                " — microsoft edge", " - microsoft edge",
                " — opera", " - opera",
                " and 1 more page", " and 2 more page",
                "chat.openai.com", "claude.ai",   # Web-Claude nicht mitzaehlen
                "anthropic.com",
            )
            for title in _win_list_windows():
                t = title.lower()
                if not t or t.strip() == _OWN_APP_TITLE_EXACT:
                    continue
                if "claude" not in t:
                    continue
                if any(b in t for b in browser_hints):
                    continue
                active = True
                break
        # KEIN mtime-Fallback mehr: Buddy soll direkt verschwinden wenn die
        # Claude-CLI geschlossen wird – nicht noch 5 Min nach Aktivitaet
        # sichtbar bleiben. Erkennung nur ueber laufenden Prozess + offenes
        # Fenster.
    except Exception:
        pass
    _CLAUDE_CACHE["active"] = active
    return active


def _win_list_windows():
    """Liste sichtbarer Fenstertitel (Duplikate raus). Fuer den Picker im
    Buddy-Tab."""
    if not _IS_WIN:
        return []
    try:
        u = ctypes.windll.user32
        seen = []
        seen_set = set()

        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                      ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _lparam):
            if not u.IsWindowVisible(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            t = (buf.value or "").strip()
            if t and t not in seen_set and len(t) < 200:
                seen_set.add(t)
                seen.append(t)
            return True

        u.EnumWindows(EnumProc(cb), 0)
        return sorted(seen, key=str.lower)
    except Exception:
        return []


def _latest_session_mtime(projects_dir, max_files=200):
    """Neueste mtime aller .jsonl-Dateien unter projects_dir. 0 wenn nichts
    gefunden. Bricht nach `max_files` ab um die Latenz klein zu halten."""
    if not projects_dir or not os.path.isdir(projects_dir):
        return 0.0
    latest = 0.0
    count = 0
    try:
        for entry in os.scandir(projects_dir):
            if not entry.is_dir():
                continue
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_file() and sub.name.endswith(".jsonl"):
                        try:
                            m = sub.stat().st_mtime
                            if m > latest:
                                latest = m
                        except OSError:
                            pass
                        count += 1
                        if count >= max_files:
                            return latest
            except OSError:
                pass
    except OSError:
        pass
    return latest


def _draw_frame_on_canvas(canvas, style, w, h, pad, color, label, chroma):
    """Zeichnet Rahmen-Layer auf einem tk.Canvas und gibt die IDs zurueck.
    Zerlegt in eine Funktion pro Design."""
    if style == "off":
        return []
    if style in ("classic", "webcam"):
        return _draw_frame_classic(canvas, w, h, pad, color, label)
    if style == "neon":
        return _draw_frame_neon(canvas, w, h, pad, color, label)
    if style == "panel":
        return _draw_frame_panel(canvas, w, h, pad, color, label)
    return []


def _draw_frame_classic(canvas, w, h, pad, color, label):
    """Tech-Rahmen mit achteckigen Ecken, Nameplate unten, LIVE-Dot."""
    ids = []
    cut = max(4, pad["l"] // 2)
    dark = "#14100e"
    darker = _shade_hex(color, 0.55)
    accent_dim = _shade_hex(color, 0.75)
    cream = "#F1EBDD"

    outer = [cut, 0, w - cut, 0, w, cut, w, h - cut,
             w - cut, h, cut, h, 0, h - cut, 0, cut]
    ids.append(canvas.create_polygon(outer, fill=color, outline=""))

    cam_l = pad["l"]; cam_t = pad["t"]
    cam_r = w - pad["r"]; cam_b = h - pad["b"] + 2
    ids.append(canvas.create_rectangle(cam_l, cam_t, cam_r, cam_b,
                                       fill=dark, outline=""))

    corner_len = max(4, pad["l"] // 2)
    for cx, cy, dx, dy in (
        (cam_l, cam_t,  1,  1), (cam_r, cam_t, -1,  1),
        (cam_l, cam_b,  1, -1), (cam_r, cam_b, -1, -1),
    ):
        ids.append(canvas.create_line(cx, cy, cx + dx * corner_len, cy,
                                      fill=color, width=2))
        ids.append(canvas.create_line(cx, cy, cx, cy + dy * corner_len,
                                      fill=color, width=2))

    stripe_w = max(6, w // 8)
    top_y = pad["t"] // 2
    for dx in (-stripe_w - 4, 0, stripe_w + 4):
        cx = w // 2 + dx
        ids.append(canvas.create_line(cx - 3, top_y, cx + 3, top_y,
                                      fill=darker, width=2))

    plate_top = h - pad["b"] + 3
    plate_bot = h - 3
    plate_half = min(w // 2 - 6, max(24, int(w * 0.36)))
    plate_cx = w // 2
    trap = [plate_cx - plate_half + 6, plate_top,
            plate_cx + plate_half - 6, plate_top,
            plate_cx + plate_half, plate_bot,
            plate_cx - plate_half, plate_bot]
    ids.append(canvas.create_polygon(trap, fill=darker, outline=""))
    ids.append(canvas.create_line(
        plate_cx - plate_half + 8, plate_top + 1,
        plate_cx + plate_half - 8, plate_top + 1,
        fill=accent_dim, width=1))
    font_size = max(6, min(11, (plate_bot - plate_top) - 4))
    ids.append(canvas.create_text(
        plate_cx, (plate_top + plate_bot) // 2,
        text=(label or "CLAWD").upper()[:7],
        fill=cream, font=("Segoe UI", font_size, "bold")))

    dot_r = max(2, pad["t"] // 3)
    dot_cx = w - pad["r"] - dot_r - 2
    dot_cy = pad["t"] // 2
    ids.append(canvas.create_oval(
        dot_cx - dot_r, dot_cy - dot_r,
        dot_cx + dot_r, dot_cy + dot_r,
        fill="#ff3a5a", outline=""))
    return ids


def _draw_frame_neon(canvas, w, h, pad, color, label):
    """Doppelte leuchtende Kontur, transparent innen, kein Nameplate.
    Ausserer Ring in dunklerem Ton, innerer in Vollton – wirkt wie Glow."""
    ids = []
    dim = _shade_hex(color, 0.45)
    # Aeussere weite duenne Linie
    ids.append(canvas.create_rectangle(0, 0, w - 1, h - 1,
                                       outline=dim, width=2))
    # Innere dickere leuchtende Linie
    inset = max(2, pad["l"] // 2)
    ids.append(canvas.create_rectangle(inset, inset, w - 1 - inset, h - 1 - inset,
                                       outline=color, width=2))
    # Kleine Ecken-Akzente (Diagonal-Striche)
    corner = max(3, pad["l"] // 2)
    for cx, cy, dx, dy in (
        (0, 0, 1, 1), (w - 1, 0, -1, 1),
        (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1),
    ):
        ids.append(canvas.create_line(
            cx, cy, cx + dx * corner, cy + dy * corner,
            fill=color, width=2))
    return ids


def _draw_frame_panel(canvas, w, h, pad, color, label):
    """Flache Titelleiste oben mit Live-Dot + Text, dunkle Cam-Flaeche,
    schmaler Akzentrand unten."""
    ids = []
    dark = "#14100e"
    darker = _shade_hex(color, 0.35)
    cream = "#F1EBDD"

    # Hauptrahmen als voll gefuelltes Rechteck
    ids.append(canvas.create_rectangle(0, 0, w - 1, h - 1,
                                       fill=darker, outline=""))
    # Titelleiste oben in Akzentfarbe
    title_h = pad["t"]
    ids.append(canvas.create_rectangle(0, 0, w, title_h,
                                       fill=color, outline=""))
    # Cam-Flaeche
    ids.append(canvas.create_rectangle(pad["l"], pad["t"],
                                       w - pad["r"], h - pad["b"],
                                       fill=dark, outline=""))
    # LIVE-Dot ganz links in der Titelleiste
    dot_r = max(2, title_h // 4)
    dot_cx = pad["l"] + dot_r + 2
    dot_cy = title_h // 2
    ids.append(canvas.create_oval(
        dot_cx - dot_r, dot_cy - dot_r,
        dot_cx + dot_r, dot_cy + dot_r,
        fill="#ff3a5a", outline=""))
    # Titel-Text daneben
    font_size = max(6, min(10, title_h - 4))
    ids.append(canvas.create_text(
        dot_cx + dot_r + 5, dot_cy,
        anchor="w",
        text=(label or "CLAWD").upper()[:10],
        fill=cream, font=("Segoe UI", font_size, "bold")))
    return ids


def _resolved_frame_style(bud):
    """Frame-Style aus Config lesen. Migration: webcam/neon/panel -> classic."""
    st = bud.get("frame_style")
    if st in ("webcam", "neon", "panel"):
        return "classic"
    if st in ("off", "classic"):
        return st
    return "off"


def _frame_pad(style, scale):
    """Padding pro Kante fuer die verschiedenen Rahmen-Styles."""
    if style == "classic":
        b = max(9, scale * 3)
        return {"l": b, "r": b, "t": b, "b": b + max(12, scale * 3), "style": "classic"}
    if style == "neon":
        b = max(6, scale * 2)
        return {"l": b, "r": b, "t": b, "b": b, "style": "neon"}
    if style == "panel":
        b = max(6, scale * 2)
        return {"l": b, "r": b, "t": b + max(12, scale * 3), "b": b, "style": "panel"}
    return {"l": 0, "r": 0, "t": 0, "b": 0, "style": "off"}


def _shade_hex(hex_color, factor):
    """Multipliziert alle RGB-Kanaele mit `factor`. Fuer dunklere Toene."""
    try:
        c = hex_color.lstrip("#")
        r = min(255, max(0, int(int(c[0:2], 16) * factor)))
        g = min(255, max(0, int(int(c[2:4], 16) * factor)))
        b = min(255, max(0, int(int(c[4:6], 16) * factor)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


class BuddyController:
    """Zeigt einen kleinen Clawd-Buddy als frameloses, transparentes,
    always-on-top Tkinter-Fenster. Laeuft in einem Daemon-Thread. Wechselt
    die Animation abhaengig von der Aktivitaet in ~/.claude/projects/*."""

    _TRANSPARENT = "magenta"        # Chroma-Key (unwahrscheinlich in Sprites)
    _FRAME_MS = 120                 # ~8 fps – reicht fuer 8-24 Frame-Anims, spart CPU
    _POLL_MS = 300                  # Zustands-/Fokus-Check-Rate
    _MTIME_CACHE_S = 2.0            # nur alle 2s Dateisystem abfragen
    _FG_CHECK_EVERY = 3             # foreground-title nur alle N ticks (~360 ms)

    def __init__(self, api):
        self.api = api              # -> hat .settings und ._projects_dir()
        self._thread = None
        self._alive = False
        self._q = queue.Queue()     # Commands aus dem UI-Thread
        self._pulse = 0             # fuer die kurzen "surprise"-Momente

    # ---- oeffentliche API (aus Api heraus aufgerufen) ----
    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.is_alive():
            return
        if not BUDDY_ANIMS:
            return                  # Sprites konnten nicht dekodiert werden
        self._alive = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="BuddyThread")
        self._thread.start()

    def stop(self):
        if not self.is_alive():
            return
        self._q.put(("quit", None))

    def push(self, key=None):
        """Buddy weiss von aussen dass sich was geaendert hat (Groesse,
        Sichtbarkeit, ...). Bei disabled -> stop, bei enabled+aus -> start."""
        s = self.api.settings.get("buddy", {})
        if not s.get("enabled"):
            self.stop()
            return
        if not self.is_alive():
            self.start()
            return
        self._q.put(("refresh", key))

    def surprise(self):
        """Kurze 'surprise'-Animation ausloesen (z.B. Test-Button)."""
        if self.is_alive():
            self._q.put(("pulse", "surprise"))

    def _notify_limit_reset(self):
        """Zeigt eine Windows-Notification via Tray-Icon wenn das Claude-Limit
        sich zurueckgesetzt hat. Nur wenn User es in Settings erlaubt hat."""
        if not self.api.settings.get("notify_limit_reset", True):
            return
        tray = getattr(self.api, "_tray", None)
        if not tray or not tray.icon:
            return
        try:
            tray.icon.notify(
                "Dein Claude-Limit ist zurueck – weitermachen!",
                "Clawd")
        except Exception:
            pass

    def preview_anim(self, name, seconds=3.0):
        """Zeigt eine bestimmte Animation fuer `seconds` Sekunden – ueberschreibt
        die Auto-Erkennung waehrenddessen."""
        if self.is_alive():
            self._q.put(("preview", (name, float(seconds))))

    def jump_to(self, x, y):
        """Buddy exakt auf (x,y) setzen."""
        if self.is_alive():
            self._q.put(("jump", (int(x), int(y))))

    def place_mode(self, on_done=None):
        """Positionier-Modus: Buddy pulsiert damit man ihn leicht findet,
        und der `on_done`-Callback wird nach dem ersten Drop aufgerufen
        (typisch: Hauptfenster wiederherstellen)."""
        self._on_place_done = on_done
        if self.is_alive():
            self._q.put(("place", True))

    # ---- interner Thread ----
    def _run(self):
        try:
            import tkinter as tk
        except Exception:
            self._alive = False
            return

        s = dict(self.api.settings.get("buddy", {}))
        scale = max(2, min(10, int(s.get("size", 4))))
        opacity = max(20, min(100, int(s.get("opacity", 100)))) / 100.0
        x, y = int(s.get("x", 200)), int(s.get("y", 200))

        root = tk.Tk()
        root.withdraw()  # spaeter deiconify, damit initial kein Flackern
        try:
            root.title("Clawd")
        except Exception:
            pass
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", self._TRANSPARENT)
        except Exception:
            pass
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass
        root.configure(bg=self._TRANSPARENT)

        # Rahmen einlesen und Fenstermaße daraus ableiten. Sprite bleibt
        # immer 20*scale, das Fenster wird um Padding groesser.
        frame_style = _resolved_frame_style(s)
        frame_pad = _frame_pad(frame_style, scale)
        px_sprite = 20 * scale
        px_w = px_sprite + frame_pad["l"] + frame_pad["r"]
        px_h = px_sprite + frame_pad["t"] + frame_pad["b"]
        root.geometry(f"{px_w}x{px_h}+{x}+{y}")

        canvas = tk.Canvas(root, width=px_w, height=px_h,
                           bg=self._TRANSPARENT,
                           highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)

        # Rahmen (Layer 0) und Sprite-Image (Layer 1) auf Canvas.
        frame_items = _draw_frame_on_canvas(
            canvas, frame_style, px_w, px_h, frame_pad,
            s.get("frame_color") or "#ec7456",
            s.get("frame_label") or "CLAWD",
            self._TRANSPARENT)

        # PhotoImage als Zeichenflaeche – Sprite zentriert im Innenbereich
        img = tk.PhotoImage(width=px_sprite, height=px_sprite)
        sprite_id = canvas.create_image(frame_pad["l"], frame_pad["t"],
                                        anchor="nw", image=img)
        canvas.image = img
        # Positionier-Highlight (unsichtbar bis place_mode)
        place_ring = canvas.create_rectangle(
            1, 1, px_w - 1, px_h - 1,
            outline="#ffd66b", width=3, state="hidden")

        def rebuild_frame(new_style, new_color, new_label, new_scale):
            """Loescht alle Frame-Layer und zeichnet sie neu; passt Fenster-,
            Canvas- und Bildgroesse an."""
            nonlocal frame_style, frame_pad, px_sprite, px_w, px_h, frame_items
            # Sprite-Zeichenflaeche und Fenster neu dimensionieren
            frame_style = new_style
            frame_pad = _frame_pad(new_style, new_scale)
            px_sprite = 20 * new_scale
            px_w = px_sprite + frame_pad["l"] + frame_pad["r"]
            px_h = px_sprite + frame_pad["t"] + frame_pad["b"]
            try:
                img.configure(width=px_sprite, height=px_sprite)
                canvas.configure(width=px_w, height=px_h)
                root.geometry(f"{px_w}x{px_h}")
                canvas.coords(sprite_id, frame_pad["l"], frame_pad["t"])
                canvas.coords(place_ring, 1, 1, px_w - 1, px_h - 1)
            except Exception:
                pass
            # Alte Frame-Layer weg, neue drauf
            for fid in frame_items:
                try: canvas.delete(fid)
                except Exception: pass
            frame_items = _draw_frame_on_canvas(
                canvas, frame_style, px_w, px_h, frame_pad,
                new_color, new_label, self._TRANSPARENT)
            # Sprite und place_ring wieder in den Vordergrund heben
            try:
                canvas.tag_raise(sprite_id)
                canvas.tag_raise(place_ring)
            except Exception:
                pass
            state["px_w"] = px_w
            state["px_h"] = px_h
            state["frame_style"] = frame_style
            state["frame_color"] = new_color
            state["frame_label"] = new_label
            # Render-Cache wegwerfen – neuer BG oder Groesse.
            render_cache.clear()
            last_drawn["key"] = None

        state = {
            "scale": scale,
            "opacity": opacity,
            "anim": "idle breathe",
            "frame": 0,
            "frame_style": frame_style,
            "frame_color": s.get("frame_color") or "#ec7456",
            "frame_label": s.get("frame_label") or "CLAWD",
            "px_w": px_w,
            "px_h": px_h,
            "live_pulse": 0.0,
            "last_mtime_check": 0.0,
            "last_mtime": 0.0,
            "activity_state": "idle",
            "surprise_until": 0.0,
            "placing": False,          # Position-Modus: dickes Highlight-Rechteck
            "place_pulse": 0.0,
            "preview_until": 0.0,
            "preview_anim": "",
            "overlay": None,           # Vollflaechen-Toplevel im Platzier-Modus
            "current_alpha": 0.0,      # tatsaechliche Fenster-Deckkraft (fuer Fade)
            "target_alpha": 0.0,
            "was_visible": False,      # letzter apply_visibility-Zustand
            "tick": 0,
            "hover": False,            # Maus ueber Buddy -> transparent machen
        }

        # ---- Drag & Drop ----
        drag = {"x": 0, "y": 0, "moved": False}

        def on_press(e):
            drag["x"] = e.x
            drag["y"] = e.y
            drag["moved"] = False

        def on_drag(e):
            nx = root.winfo_x() + e.x - drag["x"]
            ny = root.winfo_y() + e.y - drag["y"]
            # Raster + Bildschirmrand-Snap
            size_px = state.get("px_w", 20 * state["scale"])
            nx, ny = _snap_position(nx, ny, size_px)
            root.geometry(f"+{nx}+{ny}")
            drag["moved"] = True

        def on_release(e):
            if drag["moved"]:
                nx, ny = root.winfo_x(), root.winfo_y()
                bud = self.api.settings.setdefault("buddy", {})
                bud["x"], bud["y"] = nx, ny
                try:
                    save_json(SETTINGS_FILE, self.api.settings)
                except Exception:
                    pass
            if state["placing"] and drag["moved"]:
                end_place_mode()

        canvas.bind("<Button-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        # Doppelklick oder Rechtsklick -> ausblenden
        canvas.bind("<Double-Button-1>",
                    lambda e: self._q.put(("hide_toggle", None)))
        canvas.bind("<Button-3>",
                    lambda e: self._q.put(("hide_toggle", None)))
        # Maus rein/raus -> sofort transparent damit man drunter sieht
        def _on_enter(e):
            state["hover"] = True
            # Sofort auf 15% Deckkraft (kein Fade)
            try:
                target = min(state["opacity"], 0.15)
                state["current_alpha"] = target
                state["target_alpha"] = target
                root.attributes("-alpha", target)
            except Exception:
                pass
        def _on_leave(e):
            state["hover"] = False
            # Sofort zurueck auf normale Deckkraft (kein Fade)
            try:
                if state.get("was_visible"):
                    op = state["opacity"]
                    state["current_alpha"] = op
                    state["target_alpha"] = op
                    root.attributes("-alpha", op)
            except Exception:
                pass
        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)

        # ---- Overlay fuer Platzier-Modus ----
        def virtual_desktop_bounds():
            if _IS_WIN:
                try:
                    u = ctypes.windll.user32
                    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                            u.GetSystemMetrics(78), u.GetSystemMetrics(79))
                except Exception:
                    pass
            return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())

        def end_place_mode(save_pos=True):
            if not state["placing"]:
                return
            state["placing"] = False
            try:
                canvas.itemconfigure(place_ring, state="hidden")
            except Exception:
                pass
            ov = state.get("overlay")
            if ov is not None:
                try:
                    ov.destroy()
                except Exception:
                    pass
                state["overlay"] = None
            cb = getattr(self, "_on_place_done", None)
            if cb:
                try:
                    cb()
                except Exception:
                    pass

        def build_overlay():
            try:
                vx, vy, vw, vh = virtual_desktop_bounds()
                ov = tk.Toplevel(root)
                ov.overrideredirect(True)
                ov.attributes("-topmost", True)
                ov.attributes("-alpha", 0.42)
                ov.configure(bg="#0a0b0d")
                ov.geometry(f"{vw}x{vh}+{vx}+{vy}")
                cv = tk.Canvas(ov, bg="#0a0b0d",
                               highlightthickness=0, bd=0)
                cv.pack(fill="both", expand=True)
                # Feinraster
                for xi in range(0, vw, 20):
                    cv.create_line(xi, 0, xi, vh, fill="#3a3d42")
                for yi in range(0, vh, 20):
                    cv.create_line(0, yi, vw, yi, fill="#3a3d42")
                # 100er-Raster kraeftiger
                for xi in range(0, vw, 100):
                    cv.create_line(xi, 0, xi, vh, fill="#5c6068", width=1)
                for yi in range(0, vh, 100):
                    cv.create_line(0, yi, vw, yi, fill="#5c6068", width=1)
                # Nur ESC bricht ab – ein Klick soll den Buddy greifen koennen,
                # nicht das Overlay treffen.
                ov.bind("<Escape>",
                        lambda e: end_place_mode(save_pos=False))
                cv.bind("<Escape>",
                        lambda e: end_place_mode(save_pos=False))
                # Overlay unter dem Buddy halten (kein Click-Stealing) – erst
                # das Overlay bauen, dann den Buddy re-topmost setzen und
                # explizit anheben. Beide bleiben topmost; der zuletzt
                # angehobene liegt vorn.
                try:
                    ov.update_idletasks()
                    root.attributes("-topmost", False)
                    root.attributes("-topmost", True)
                    root.lift()
                    # ESC-Fokus - ohne den kommt kein Escape an
                    cv.focus_set()
                    ov.focus_force()
                except Exception:
                    pass
                return ov
            except Exception:
                return None

        # ---- Sichtbarkeits-Logik (mit throttled foreground-check) ----
        fg_cache = {"title": "", "tick": -999}

        def _fg_title(tick_count):
            if tick_count - fg_cache["tick"] >= self._FG_CHECK_EVERY:
                fg_cache["title"] = _win_foreground_title().lower()
                fg_cache["tick"] = tick_count
            return fg_cache["title"]

        def desired_visible():
            bud = self.api.settings.get("buddy", {})
            if not bud.get("enabled"):
                return False
            # Buddy-Tab: immer sichtbar (auch waehrend Foreground-Racing).
            if getattr(self.api, "_current_view", "") == "buddy":
                return True
            fg = _fg_title(state.get("tick", 0))
            # Session Browser vorne (auf anderem Tab) -> Buddy weg.
            if fg.strip() == _OWN_APP_TITLE_EXACT:
                return False
            mode = bud.get("visibility", "when_claude")
            if mode == "always":
                return True
            if mode == "when_window":
                needle = (bud.get("target_window") or "").lower().strip()
                if not needle:
                    return True
                return needle in fg
            if mode == "when_claude":
                return _claude_context_active()
            return True

        _visible = {"v": None}

        def apply_visibility():
            want = desired_visible()
            # Fade-Ziel aktualisieren – nicht sofort withdraw/deiconify.
            # Bei Maus-Hover deutlich transparenter (max 20% der eingest. Opacity).
            if want:
                if state.get("hover"):
                    state["target_alpha"] = min(state["opacity"], 0.15)
                else:
                    state["target_alpha"] = state["opacity"]
            else:
                state["target_alpha"] = 0.0
            if want and not state["was_visible"]:
                # Reingekommen -> Fenster zeigen (transparent) und Fade starten
                try:
                    root.attributes("-alpha", 0.0)
                    state["current_alpha"] = 0.0
                    root.deiconify()
                except Exception:
                    pass
            state["was_visible"] = want
            _visible["v"] = want

        def step_fade():
            # Naehert current_alpha an target_alpha an. ~180 ms Total.
            cur = state["current_alpha"]
            tgt = state["target_alpha"]
            if abs(cur - tgt) < 0.02:
                if cur != tgt:
                    state["current_alpha"] = tgt
                    try:
                        root.attributes("-alpha", tgt)
                    except Exception:
                        pass
                    if tgt <= 0.001 and not state["was_visible"]:
                        try:
                            root.withdraw()
                        except Exception:
                            pass
                return
            step = 0.12 if tgt > cur else -0.12
            new = cur + step
            if (step > 0 and new > tgt) or (step < 0 and new < tgt):
                new = tgt
            state["current_alpha"] = new
            try:
                root.attributes("-alpha", max(0.0, min(1.0, new)))
            except Exception:
                pass

        # ---- Aktivitaets-Detection ----
        # Modifikationszeit alle 2 s, Limit-Check nur bei frischer Aktivitaet
        # (max alle 15 s) und mit sehr strengen Mustern damit keine normalen
        # Chat-Erwaehnungen von „rate limit" false positive triggern.
        state["last_limit_check"] = 0.0
        state["is_limited"] = False

        def detect_state():
            now = time.time()
            if now - state["last_mtime_check"] > self._MTIME_CACHE_S:
                state["last_mtime_check"] = now
                pdir = ""
                try:
                    pdir = self.api._projects_dir()
                except Exception:
                    pdir = ""
                state["last_mtime"] = _latest_session_mtime(pdir)
            if state["last_mtime"] <= 0:
                return "none"
            age = now - state["last_mtime"]
            if age < 300 and now - state["last_limit_check"] > 15:
                state["last_limit_check"] = now
                try:
                    pdir = self.api._projects_dir()
                    new_limited = _latest_jsonl_hits_limit(pdir)
                except Exception:
                    new_limited = False
                # Uebergang von limitiert -> frei: Notification wenn gewuenscht
                if state["is_limited"] and not new_limited:
                    try:
                        self._notify_limit_reset()
                    except Exception:
                        pass
                state["is_limited"] = new_limited
            if state["is_limited"] and age < 3600:
                return "limit"
            if age < 3:
                return "active"
            if age < 15:
                return "thinking"
            if age < 90:
                return "recent"
            if age < 600:
                return "idle"
            return "sleep"

        # ---- Anim wechseln ----
        def choose_anim():
            bud = self.api.settings.get("buddy", {})
            now = time.time()
            if now < state["preview_until"] and state["preview_anim"] in BUDDY_ANIMS:
                return state["preview_anim"]
            if now < state["surprise_until"]:
                return BUDDY_STATE_MAP["surprise"]
            if bud.get("party"):
                return BUDDY_STATE_MAP["party"]
            act = detect_state()
            state["activity_state"] = act
            return BUDDY_STATE_MAP.get(act, "idle breathe")

        # ---- Rendering (mit Frame-Cache) ----
        # Cache-Key = (anim_name, frame_idx, scale, bg_fill) -> list[20] row-strings.
        # Da Animationen loopen und Groesse/BG-Farbe konstant bleiben, sparen
        # wir nach einem vollen Zyklus 100% der Zeichenkosten pro Frame.
        render_cache = {}
        # Merkmale des zuletzt geschriebenen Frames, damit wir bei unveraenderter
        # Zeichnung gar nichts machen.
        last_drawn = {"key": None}

        def render_frame():
            name = state["anim"]
            anim = BUDDY_ANIMS.get(name) or next(iter(BUDDY_ANIMS.values()))
            frames = anim["frames"]
            if not frames:
                return
            frame_idx = state["frame"] % len(frames)
            sc = state["scale"]
            bg_fill = "#14100e" if state.get("frame_style", "off") != "off" else self._TRANSPARENT
            key = (name, frame_idx, sc, bg_fill)

            if key == last_drawn["key"]:
                state["frame"] += 1
                return

            rows_data = render_cache.get(key)
            if rows_data is None:
                palette = anim["palette"]
                f = frames[frame_idx]
                rows_data = []
                for row in range(20):
                    cells = []
                    ridx = row * 20
                    for col in range(20):
                        idx = f[ridx + col]
                        if idx <= 0:
                            cells.append(bg_fill)
                        elif idx < len(palette):
                            cells.append(palette[idx])
                        else:
                            cells.append(bg_fill)
                    row_str = "{" + " ".join(
                        (" ".join([c] * sc)) for c in cells) + "}"
                    rows_data.append(row_str)
                # Cache begrenzen (nicht boesartig wachsen lassen)
                if len(render_cache) > 400:
                    render_cache.clear()
                render_cache[key] = rows_data

            try:
                for row in range(20):
                    y1 = row * sc
                    img.put(rows_data[row], to=(0, y1, 20 * sc, y1 + sc))
                last_drawn["key"] = key
            except Exception:
                pass
            state["frame"] += 1

        # ---- Command-Queue ----
        def process_cmds():
            try:
                while True:
                    cmd, val = self._q.get_nowait()
                    if cmd == "quit":
                        self._alive = False
                        try:
                            root.destroy()
                        except Exception:
                            pass
                        return False
                    elif cmd == "refresh":
                        # Buddy-Einstellungen neu einlesen
                        new = self.api.settings.get("buddy", {})
                        new_scale = max(2, min(10, int(new.get("size", 4))))
                        new_op = max(20, min(100, int(new.get("opacity", 100)))) / 100.0
                        new_style = _resolved_frame_style(new)
                        new_color = new.get("frame_color") or "#ec7456"
                        new_label = new.get("frame_label") or "CLAWD"
                        # Sprite-/Fenster-/Frame-Rebuild wenn irgendwas
                        # dimensions- oder styleaenderndes anliegt.
                        if (new_scale != state["scale"] or
                                new_style != state["frame_style"] or
                                new_color != state["frame_color"] or
                                new_label != state["frame_label"]):
                            state["scale"] = new_scale
                            rebuild_frame(new_style, new_color, new_label, new_scale)
                        if abs(new_op - state["opacity"]) > 0.001:
                            state["opacity"] = new_op
                            if state["was_visible"]:
                                state["target_alpha"] = new_op
                        _visible["v"] = None
                    elif cmd == "hide_toggle":
                        # Rechtsklick -> Buddy ausblenden (in Settings)
                        bud = self.api.settings.setdefault("buddy", {})
                        bud["enabled"] = False
                        try:
                            save_json(SETTINGS_FILE, self.api.settings)
                        except Exception:
                            pass
                        self._alive = False
                        try:
                            root.destroy()
                        except Exception:
                            pass
                        return False
                    elif cmd == "pulse":
                        state["surprise_until"] = time.time() + 1.6
                    elif cmd == "place":
                        state["placing"] = True
                        state["place_pulse"] = 0.0
                        try:
                            canvas.itemconfigure(place_ring, state="normal")
                            root.attributes("-topmost", True)
                        except Exception:
                            pass
                        # Vollflaechen-Overlay mit Grid einblenden
                        if state.get("overlay") is None:
                            state["overlay"] = build_overlay()
                    elif cmd == "preview":
                        name, seconds = val
                        if name in BUDDY_ANIMS:
                            state["preview_anim"] = name
                            state["preview_until"] = time.time() + seconds
                    elif cmd == "jump":
                        nx, ny = val
                        # Fenstergroesse inkl. Rahmen (state["px_w"]) fuer korrektes
                        # Edge-Snap, nicht nur Sprite-Groesse.
                        size_px = state.get("px_w", 20 * state["scale"])
                        nx, ny = _snap_position(nx, ny, size_px)
                        try:
                            root.geometry(f"+{nx}+{ny}")
                        except Exception:
                            pass
                        bud = self.api.settings.setdefault("buddy", {})
                        bud["x"], bud["y"] = nx, ny
                        try:
                            save_json(SETTINGS_FILE, self.api.settings)
                        except Exception:
                            pass
            except queue.Empty:
                pass
            return True

        # ---- Haupt-Loop (via after()) ----
        def tick():
            if not self._alive:
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            state["tick"] += 1
            if not process_cmds():
                return
            apply_visibility()
            step_fade()
            chosen = choose_anim()
            if chosen != state["anim"]:
                state["anim"] = chosen
                state["frame"] = 0
            if state["current_alpha"] > 0.01:
                render_frame()
            # Place-Mode: Rahmen pulsieren fuer bessere Sichtbarkeit
            if state["placing"]:
                state["place_pulse"] = (state["place_pulse"] + 0.14) % 6.283
                import math
                intensity = int(2 + 3 * (0.5 + 0.5 * math.sin(state["place_pulse"])))
                try:
                    canvas.itemconfigure(place_ring, width=intensity)
                except Exception:
                    pass
            root.after(self._FRAME_MS, tick)

        try:
            root.after(50, tick)
            root.mainloop()
        except Exception:
            pass
        finally:
            self._alive = False


# --------------------------------------------------------------------------- #
#  System-Tray (X = App in Hintergrund)
# --------------------------------------------------------------------------- #
class TrayManager:
    """System-Tray-Icon damit die App im Hintergrund weiterlaeuft wenn der
    User auf X klickt. Rechtsklick → Menue mit Oeffnen/Beenden. Linksklick
    → App wieder zeigen."""

    def __init__(self, get_window, on_quit):
        self.get_window = get_window
        self.on_quit = on_quit
        self.icon = None
        self._thread = None

    def start(self):
        if self.icon:
            return
        try:
            import pystray
            from PIL import Image
        except Exception:
            return

        icon_img = None
        # Reihenfolge: bevorzugt .ico (App-Icon, immer im Build), dann logo.png,
        # dann farbiges Fallback-Quadrat.
        for candidate in ("claude_sessions.ico", "logo.png"):
            try:
                icon_img = Image.open(_resource(candidate))
                break
            except Exception:
                continue
        if icon_img is None:
            try:
                icon_img = Image.new("RGB", (64, 64), "#ec7456")
            except Exception:
                return

        def _open(icon, item):
            self.show_main()

        def _quit(icon, item):
            try:
                self.icon.stop()
            except Exception:
                pass
            try:
                self.on_quit()
            except Exception:
                pass

        menu = pystray.Menu(
            pystray.MenuItem("Öffnen", _open, default=True),
            pystray.MenuItem("Beenden", _quit),
        )
        self.icon = pystray.Icon(
            "ClaudeSessionBrowser",
            icon=icon_img,
            title="Claude Session Browser",
            menu=menu,
        )
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="TrayThread")
        self._thread.start()

    def _run(self):
        try:
            self.icon.run()
        except Exception:
            pass

    def show_main(self):
        win = self.get_window()
        if not win:
            return
        try:
            win.show()
        except Exception:
            pass
        try:
            win.restore()
        except Exception:
            pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  API (von JavaScript aufrufbar)
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self.overrides = load_json(TITLES_FILE, {})
        self.settings = load_settings()
        self._cache = None
        self._current_view = "sessions"
        self.buddy = BuddyController(self)

    @staticmethod
    def _win():
        return webview.windows[0] if webview.windows else None

    def bind_window(self, win):
        """Merkt sich Fenstergroesse/-position/Maximierung – ressourcenschonend:
        waehrend der Nutzung nur In-Memory, gespeichert wird nur beim Schliessen."""
        s = self.settings
        self._max = bool(s.get("win_max"))
        self._geo = {
            "w": s.get("win_w") or 1180, "h": s.get("win_h") or 760,
            "x": s.get("win_x"), "y": s.get("win_y"),
        }

        def on_resized(*a):
            if len(a) >= 2 and not self._max:
                self._geo["w"], self._geo["h"] = a[0], a[1]

        def on_moved(*a):
            if len(a) >= 2 and not self._max:
                self._geo["x"], self._geo["y"] = a[0], a[1]

        def on_max(*a):
            self._max = True

        def on_restore(*a):
            self._max = False

        def on_closing(*a):
            if getattr(self, "_geo_saved", False):
                return   # nur einmal speichern (closing UND closed feuern)
            self._geo_saved = True
            self.settings["win_w"] = int(self._geo["w"])
            self.settings["win_h"] = int(self._geo["h"])
            if self._geo["x"] is not None:
                self.settings["win_x"] = int(self._geo["x"])
            if self._geo["y"] is not None:
                self.settings["win_y"] = int(self._geo["y"])
            self.settings["win_max"] = bool(self._max)
            save_json(SETTINGS_FILE, self.settings)

        win.events.resized += on_resized
        win.events.moved += on_moved
        win.events.maximized += on_max
        win.events.restored += on_restore
        win.events.closing += on_closing
        win.events.closed += on_closing   # Fallback, falls 'closing' nicht feuert

    # -- intern --
    def _projects_dir(self):
        p = self.settings.get("projects_dir")
        if p and os.path.isdir(p):
            return p
        return detect_projects_dir()

    def _sessions(self, force=False):
        if self._cache is None or force:
            self._cache = collect_sessions(self._projects_dir())
        colors = self.settings.get("session_colors", {})
        for s in self._cache:
            s["display_title"] = self.overrides.get(s["id"], s["auto_title"])
            s["color"] = colors.get(s["id"], "")
            s["when"] = fmt_time(s["mtime"])
        return self._cache

    def _state(self, force=False):
        pdir = self._projects_dir()
        return {
            "sessions": self._sessions(force),
            "settings": self.settings,
            "projects_dir": pdir,
            "found": bool(pdir and os.path.isdir(pdir)),
            "home": HOME,
            "version": VERSION,
            "onboarding_version": ONBOARDING_VERSION,
        }

    # -- von JS aufgerufen --
    def get_state(self):
        return self._state()

    def refresh(self):
        return self._state(force=True)

    def resume(self, sid, cwd, project=""):
        return resume_session(sid, cwd, self.settings, project)

    def rename(self, sid, title):
        title = (title or "").strip()
        auto = next((s["auto_title"] for s in (self._cache or []) if s["id"] == sid), "")
        if title and title != auto:
            self.overrides[sid] = title
        else:
            self.overrides.pop(sid, None)
        save_json(TITLES_FILE, self.overrides)
        return self._state()

    def set_color(self, sid, color):
        colors = self.settings.setdefault("session_colors", {})
        if color:
            colors[sid] = color
        else:
            colors.pop(sid, None)
        save_json(SETTINGS_FILE, self.settings)
        return self._state()

    def update_setting(self, key, value):
        force = False
        if key == "projects_dir":
            force = True
        self.settings[key] = value
        save_json(SETTINGS_FILE, self.settings)
        return self._state(force=force)

    def add_hidden_folder(self, path):
        if path:
            folders = self.settings.setdefault("hidden_folders", [])
            if not any(norm(f) == norm(path) for f in folders):
                folders.append(path)
                save_json(SETTINGS_FILE, self.settings)
        return self._state()

    def remove_hidden_folder(self, path):
        folders = self.settings.get("hidden_folders", [])
        self.settings["hidden_folders"] = [f for f in folders if norm(f) != norm(path)]
        save_json(SETTINGS_FILE, self.settings)
        return self._state()

    def browse_folder(self):
        win = self._win()
        res = win.create_file_dialog(webview.FOLDER_DIALOG) if win else None
        if res:
            path = res[0] if isinstance(res, (list, tuple)) else res
            return self.update_setting("projects_dir", path)
        return self._state()

    def copy(self, text):
        try:
            subprocess.run(["clip"], input=str(text), text=True, shell=True)
            return True
        except OSError:
            return False

    # -- Buddy (Clawd-Maskottchen) --
    def buddy_state(self):
        """Was der Buddy-Tab braucht: aktuelle Config + verfuegbare Animationen
        (nur Namen; Sprites werden nicht ans UI geschickt) + Preview-Palette."""
        bud = self.settings.get("buddy", {})
        anims = []
        for name, data in BUDDY_ANIMS.items():
            frames = data.get("frames", [])
            anims.append({"name": name, "frames": len(frames)})
        # Preview-Frame als Data-URL fuer den Tab (aktuelle Animation, erstes
        # Frame – reicht als Icon).
        default_name = bud.get("preview_anim") or "idle breathe"
        preview = self._buddy_preview_gif(default_name)
        # Warum ist er ggf. gerade nicht sichtbar?
        reason = ""
        if bud.get("enabled") and self.buddy.is_alive():
            mode = bud.get("visibility", "when_claude")
            if mode == "when_claude" and not _claude_context_active():
                reason = "wartet auf Claude"
            elif mode == "when_window":
                needle = (bud.get("target_window") or "").lower().strip()
                fg = _win_foreground_title().lower()
                if needle and needle not in fg:
                    reason = "wartet auf Fenster"
        return {
            "config": bud,
            "anims": anims,
            "running": self.buddy.is_alive(),
            "reason": reason,
            "have_sprites": bool(BUDDY_ANIMS),
            "state_map": BUDDY_STATE_MAP,
            "preview": preview,
            "preview_name": default_name,
        }

    def buddy_set(self, key, value):
        bud = self.settings.setdefault("buddy", dict(DEFAULT_SETTINGS["buddy"]))
        bud[key] = value
        save_json(SETTINGS_FILE, self.settings)
        if key == "enabled":
            if value:
                self.buddy.start()
            else:
                self.buddy.stop()
        else:
            self.buddy.push(key)
        return self.buddy_state()

    def buddy_windows(self):
        """Aktuelle Fensterliste fuer den Picker."""
        return _win_list_windows()

    def buddy_surprise(self):
        self.buddy.surprise()
        return {"ok": True}

    def set_autostart(self, on):
        """Autostart im Windows-Registry setzen und Setting speichern."""
        ok = set_autostart(bool(on))
        self.settings["autostart"] = bool(on)
        save_json(SETTINGS_FILE, self.settings)
        return {"ok": ok, "enabled": bool(on)}

    def buddy_apply_tray(self, on):
        """Tray-Icon starten/stoppen wenn Toggle sich aendert."""
        tray = getattr(self, "_tray", None)
        if not tray:
            return {"ok": False}
        try:
            if on:
                tray.start()
            else:
                tray.stop()
        except Exception:
            pass
        return {"ok": True}

    def buddy_real_quit(self):
        """App wirklich beenden (statt in Tray verstecken)."""
        fn = getattr(self, "_real_quit", None)
        if fn:
            try:
                fn()
            except Exception:
                pass
        return {"ok": True}

    def buddy_notify_view(self, view):
        """Wird beim Tab-Wechsel im UI aufgerufen. Speichert die aktuelle
        Ansicht im Api-Objekt (nicht persistiert) – die Buddy-Loop nutzt es,
        um den Buddy im Buddy-Tab sichtbar zu lassen, sonst zu verstecken
        waehrend der Session Browser vorne ist."""
        self._current_view = str(view or "sessions")
        return {"ok": True}

    def buddy_preview_anim(self, name):
        """Zeigt eine bestimmte Animation kurz auf dem Buddy."""
        if not self.buddy.is_alive():
            # Falls Buddy aus: kurz anwerfen, ist nicht schlimm
            bud = self.settings.setdefault("buddy", dict(DEFAULT_SETTINGS["buddy"]))
            bud["enabled"] = True
            save_json(SETTINGS_FILE, self.settings)
            self.buddy.start()
            time.sleep(0.3)
        self.buddy.preview_anim(name, 3.5)
        return {"ok": True}

    def buddy_monitors(self):
        """Alle Monitore mit Label/Groesse fuer den Picker."""
        return _win_enum_monitors()

    def buddy_anchor(self, anchor, monitor_idx=None):
        """Springt zu einem benannten Ankerpunkt (tl,tc,tr,ml,c,mr,bl,bc,br)
        auf einem bestimmten Monitor (oder dem aktuellen wenn None)."""
        bud = self.settings.setdefault("buddy", dict(DEFAULT_SETTINGS["buddy"]))
        if not bud.get("enabled"):
            bud["enabled"] = True
            save_json(SETTINGS_FILE, self.settings)
            self.buddy.start()
            time.sleep(0.4)
        scale = max(2, min(10, int(bud.get("size", 4))))
        # Rahmen berücksichtigen – das Fenster ist ggf. groesser als 20*scale.
        pad = _frame_pad(_resolved_frame_style(bud), scale)
        size_px = 20 * scale + pad["l"] + pad["r"]
        mi = int(monitor_idx) if monitor_idx is not None else None
        nx, ny = _anchor_position(anchor, size_px, int(bud.get("x", 200)),
                                  int(bud.get("y", 200)), mi)
        self.buddy.jump_to(nx, ny)
        # Optimistisch schon merken (der Buddy-Thread persistiert nochmal
        # nach dem Snap – kann leicht abweichen, dann gewinnt der Thread).
        bud["x"], bud["y"] = nx, ny
        save_json(SETTINGS_FILE, self.settings)
        return self.buddy_state()

    def buddy_place(self):
        """Positionier-Modus: Hauptfenster minimieren, Buddy pulsieren lassen,
        auf ersten Drop warten. Danach Hauptfenster wieder holen."""
        bud = self.settings.setdefault("buddy", dict(DEFAULT_SETTINGS["buddy"]))
        was_off = not bud.get("enabled")
        if was_off:
            bud["enabled"] = True
            save_json(SETTINGS_FILE, self.settings)
            self.buddy.start()
            # kurze Wartezeit bis der Tkinter-Thread hochgefahren ist
            for _ in range(30):
                if self.buddy.is_alive():
                    break
                time.sleep(0.1)

        # Fenster bleibt offen – das Grid-Overlay legt sich davor.
        self.buddy.place_mode(on_done=None)
        return {"ok": True}

    def buddy_preview(self, name):
        """Liefert einen Vorschau-Frame als PNG-Data-URL."""
        return self._buddy_preview_gif(name)

    def _buddy_preview_gif(self, name):
        """Baut aus dem ersten Frame einer Animation ein 80x80 PNG-Data-URL.
        (Fuer die Auswahl-Liste im Buddy-Tab.)"""
        anim = BUDDY_ANIMS.get(name)
        if not anim:
            return ""
        frame = anim["frames"][0]
        palette = anim["palette"]
        scale = 4
        # PNG selbst bauen ist Overkill – wir generieren stattdessen ein
        # BMP mit 4x-Scale und liefern es als data-uri.
        w = 20 * scale
        h = 20 * scale
        # 24-bit BMP, unten-nach-oben.
        row_bytes = w * 3
        pad = (4 - row_bytes % 4) % 4
        pixels = bytearray()
        for row in range(19, -1, -1):
            for _ in range(scale):
                for col in range(20):
                    idx = frame[row * 20 + col]
                    if idx <= 0:
                        r, g, b = 20, 16, 14   # dunkler App-Hintergrund
                    else:
                        hx = palette[idx] if idx < len(palette) else "#000000"
                        hx = hx.lstrip("#")
                        r = int(hx[0:2], 16); g = int(hx[2:4], 16); b = int(hx[4:6], 16)
                    for _ in range(scale):
                        pixels += bytes((b, g, r))
                pixels += bytes(pad)
        file_size = 54 + len(pixels)
        header = bytearray()
        header += b"BM"
        header += file_size.to_bytes(4, "little")
        header += b"\x00\x00\x00\x00"
        header += (54).to_bytes(4, "little")
        header += (40).to_bytes(4, "little")
        header += w.to_bytes(4, "little", signed=True)
        header += h.to_bytes(4, "little", signed=True)
        header += (1).to_bytes(2, "little")
        header += (24).to_bytes(2, "little")
        header += (0).to_bytes(4, "little")
        header += len(pixels).to_bytes(4, "little")
        header += (2835).to_bytes(4, "little")
        header += (2835).to_bytes(4, "little")
        header += (0).to_bytes(4, "little")
        header += (0).to_bytes(4, "little")
        raw = bytes(header) + bytes(pixels)
        return "data:image/bmp;base64," + base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _ssl_ctx():
        # Echte TLS-Verifizierung (der Updater laedt eine ausfuehrbare Datei, daher
        # darf TLS nicht abgeschaltet werden). Bevorzugt den Windows-Zertifikat-
        # speicher (funktioniert auch hinter TLS-Inspektion/Firewalls), sonst certifi.
        try:
            import truststore
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:
            pass
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def _remote_info(self, timeout=4):
        req = urllib.request.Request(
            UPDATE_URL, headers={"User-Agent": "ClaudeSessionBrowser"})
        with urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8"))

    def consume_update_failed_marker(self):
        """Prueft ob der letzte Update-Batch fehlgeschlagen ist (Datei-Move
        klappte nicht). Loescht den Marker und meldet True, damit die UI
        einen Toast zeigen kann."""
        marker = os.path.join(tempfile.gettempdir(),
                              "csb_update_failed.marker")
        if os.path.isfile(marker):
            try:
                os.remove(marker)
            except OSError:
                pass
            return True
        return False

    def check_update(self):
        """Fragt bei GitHub nach einer neueren Version. Unterscheidet zwischen
        Netzwerk-Fehler und "wirklich aktuell" damit die UI unterscheiden kann."""
        frozen = bool(getattr(sys, "frozen", False))
        try:
            data = self._remote_info()
            self._update_info = data
            latest = data.get("version", "0")
            avail = _vtuple(latest) > _vtuple(VERSION)
            return {"available": avail, "latest": latest, "current": VERSION,
                    "url": data.get("url", ""), "notes": data.get("notes", ""),
                    "frozen": frozen, "error": ""}
        except Exception as e:
            # Explizit Fehler-Info liefern, damit die UI "Netzwerkfehler"
            # von "aktuelle Version" trennen kann.
            return {"available": False, "current": VERSION, "frozen": frozen,
                    "error": type(e).__name__ + ": " + str(e)[:120]}

    def install_update(self):
        """Laedt die neue .exe, ersetzt die laufende und startet neu.
        Einstellungen/Daten in ~/.claude bleiben unberuehrt."""
        try:
            data = getattr(self, "_update_info", None) or self._remote_info()
        except Exception:
            return {"ok": False, "error": "Kein Internet / Repo nicht erreichbar."}
        page = data.get("url") or \
            "https://github.com/juppeee/claude-session-browser/releases/latest"
        exe_url = data.get("exe_url") or ""

        # Im Entwicklungsmodus (.py, keine .exe): nur Release-Seite oeffnen
        if not getattr(sys, "frozen", False):
            webbrowser.open(page)
            return {"ok": False, "reason": "dev", "opened": True}
        if not exe_url:
            webbrowser.open(page)
            return {"ok": False, "reason": "no_exe_url", "opened": True}

        if getattr(self, "_installing", False):
            return {"ok": False, "error": "Update läuft bereits."}
        self._installing = True

        win = self._win()

        def js(code):
            if win:
                try:
                    win.evaluate_js(code)
                except Exception:
                    pass

        part = os.path.join(tempfile.gettempdir(), "ClaudeSessionBrowser_update.exe.part")
        try:
            import time
            cur = sys.executable
            target_dir = os.path.dirname(cur) or "."
            # Download zuerst in eine .part-Datei in einem IMMER beschreibbaren Temp-
            # Ordner; erst nach vollstaendiger Pruefung in die finale .new umbenennen.
            new = os.path.join(tempfile.gettempdir(), "ClaudeSessionBrowser_update.exe")
            part = new + ".part"
            req = urllib.request.Request(
                exe_url, headers={"User-Agent": "ClaudeSessionBrowser"})
            with urllib.request.urlopen(req, timeout=120, context=self._ssl_ctx()) as r, \
                    open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                last = -1
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        p = int(done * 100 / total)
                        if p != last:
                            last = p
                            js("window.updateProgress&&updateProgress(%d)" % p)

            # Vollstaendigkeit pruefen: heruntergeladene Groesse muss exakt passen,
            # sonst wuerde eine kaputte .exe getauscht -> "Failed to load Python DLL".
            size = os.path.getsize(part)
            if (total and size != total) or size < 2_000_000:
                try:
                    os.remove(part)
                except OSError:
                    pass
                return {"ok": False,
                        "error": "Download unvollständig – bitte erneut versuchen."}
            # MZ-Header pruefen (gueltige .exe?)
            with open(part, "rb") as f:
                if f.read(2) != b"MZ":
                    os.remove(part)
                    return {"ok": False, "error": "Heruntergeladene Datei ist keine gültige .exe."}

            # Integritaets-Pruefung ueber SHA-256, wenn im version.json angegeben.
            # Feld ist optional (aeltere version.json ohne sha256 laufen ohne Check
            # durch – Backward-Compat) aber wenn angegeben MUSS er passen.
            import hashlib
            expected = str(data.get("sha256") or "").strip().lower()
            if expected:
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    try: os.remove(part)
                    except OSError: pass
                    return {"ok": False,
                            "error": "Ungültiger SHA-256 im Server-Manifest."}
                h = hashlib.sha256()
                with open(part, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                actual = h.hexdigest()
                if actual != expected:
                    try: os.remove(part)
                    except OSError: pass
                    return {"ok": False,
                            "error": ("Integritäts-Prüfung fehlgeschlagen "
                                      "(SHA-256 stimmt nicht). Update abgebrochen.")}

            if os.path.exists(new):
                os.remove(new)
            os.replace(part, new)   # atomar

            # Ist der Zielordner beschreibbar? (C:\ etc. brauchen Admin)
            writable = True
            try:
                _t = os.path.join(target_dir, ".csb_write_test")
                with open(_t, "w") as _f:
                    _f.write("x")
                os.remove(_t)
            except OSError:
                writable = False

            # Batch: wartet bis die laufende .exe frei ist, tauscht aus, startet neu.
            # Laeuft komplett unsichtbar (CREATE_NO_WINDOW) -> kein Ping-Fenster.
            # Bricht nach ~60 Versuchen ab und startet die App trotzdem wieder
            # (kein Endlos-Geist-Prozess). Relaunch ueber explorer.exe -> laeuft
            # als normaler Nutzer (auch wenn der Tausch elevated lief).
            bat = os.path.join(tempfile.gettempdir(), "csb_update.bat")
            marker = os.path.join(tempfile.gettempdir(),
                                  "csb_update_failed.marker")
            with open(bat, "w", encoding="utf-8") as f:
                f.write(
                    "@echo off\r\n"
                    'set "CUR=' + cur + '"\r\n'
                    'set "NEW=' + new + '"\r\n'
                    'set "FAIL=' + marker + '"\r\n'
                    'if exist "%FAIL%" del "%FAIL%"\r\n'
                    "set /a n=0\r\n"
                    ":wait\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"
                    'move /y "%NEW%" "%CUR%" >nul 2>&1\r\n'
                    'if not exist "%NEW%" goto done\r\n'
                    "set /a n+=1\r\n"
                    "if %n% lss 60 goto wait\r\n"
                    "rem Move gescheitert – Fehler-Marker schreiben\r\n"
                    'echo swap failed > "%FAIL%"\r\n'
                    ":done\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"
                    'explorer.exe "%CUR%"\r\n'
                    'del "%~f0"\r\n'
                )

            js("window.downloadDone&&downloadDone()")
            time.sleep(2.6)        # die "Bereit!"-Animation abspielen lassen

            NOWIN = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            if writable:
                subprocess.Popen(["cmd", "/c", bat], creationflags=NOWIN)
            else:
                # Geschuetzter Ort -> Tausch mit Adminrechten (einmal UAC), unsichtbar
                ps = ("Start-Process -FilePath cmd.exe "
                      "-ArgumentList '/c','\"%s\"' -Verb RunAs -WindowStyle Hidden" % bat)
                subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                                  "-Command", ps], creationflags=NOWIN)
            if win:
                win.destroy()      # entsperrt die .exe -> Batch tauscht & startet neu
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            self._installing = False
            try:
                if os.path.exists(part):
                    os.remove(part)   # angefangenen Download aufraeumen
            except OSError:
                pass

    def open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def toggle_fullscreen(self):
        win = self._win()
        if win:
            win.toggle_fullscreen()

    def minimize(self):
        win = self._win()
        if win:
            win.minimize()

    def close(self):
        win = self._win()
        if win:
            win.destroy()


# --------------------------------------------------------------------------- #
#  HTML / CSS / JS
# --------------------------------------------------------------------------- #
def logo_data_uri():
    try:
        with open(_resource("logo.png"), "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""


def build_html():
    return HTML_TEMPLATE.replace("__LOGO__", logo_data_uri())


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --bg:#14100e; --surface:#1f1814; --surface2:#2b211b; --row:#191412;
    --row-alt:#1e1814; --border:#2d231d; --fg:#f3ece7; --muted:#9a8c83;
    --accent:#ec7456; --accent2:#f5926f; --select:#4a3327;
  }
  *{box-sizing:border-box; margin:0; padding:0}
  html,body{height:100%}
  body{
    font-family:"Segoe UI",system-ui,sans-serif; color:var(--fg);
    background:var(--bg); overflow:hidden; user-select:none;
    font-size:14px; color-scheme:dark;   /* native Steuerelemente (Dropdown etc.) dunkel */
  }
  .app{display:flex; flex-direction:column; height:100vh}

  /* ---- Titelleiste ---- */
  .titlebar{
    height:44px; display:flex; align-items:center; gap:10px; padding:0 6px 0 12px;
    background:linear-gradient(90deg,#1a130f,#1f1714);
    border-bottom:1px solid var(--border); flex:none;
  }
  .titlebar .logo{width:26px; height:26px; flex:none}
  .l-spin{transform-origin:512px 512px; animation:l-spin 28s linear infinite}
  .l-pulse{transform-origin:512px 512px; animation:l-pulse 4s ease-in-out infinite}
  @keyframes l-spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
  @keyframes l-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.04)}}
  .titlewrap{display:flex; align-items:center; gap:13px}
  .hlogo{width:34px; height:34px; flex:none}
  .titlebar .tt{font-weight:600; font-size:13px; color:var(--muted); letter-spacing:.3px}
  .drag{flex:1; height:100%}
  .winbtns{display:flex; gap:2px}
  .winbtn{
    width:42px; height:30px; border:none; background:transparent; color:var(--muted);
    border-radius:8px; cursor:pointer; font-size:14px; display:grid; place-items:center;
  }
  .winbtn:hover{background:var(--surface2); color:var(--fg)}
  .winbtn.close:hover{background:#e54b58; color:#fff}

  /* ---- Tabs ---- */
  .tabs{display:flex; gap:4px; padding:10px 18px 0; background:var(--bg); flex:none}
  .tab{
    padding:9px 18px; font-weight:600; color:var(--muted); cursor:pointer;
    border-radius:9px 9px 0 0; position:relative; font-size:13.5px;
  }
  .tab:hover{color:var(--fg)}
  .tab.active{color:var(--fg)}
  .tab.active::after{
    content:""; position:absolute; left:14px; right:14px; bottom:-1px; height:2.5px;
    background:var(--accent); border-radius:3px;
  }

  .updatebar{display:none; align-items:center; gap:11px; margin:4px 18px 0; padding:10px 14px;
    background:rgba(236,116,86,.13); border:1px solid var(--accent); border-radius:11px; color:var(--accent2)}
  .updatebar.show{display:flex}
  .updatebar .utext{font-weight:700; color:var(--fg)}
  .updatebar .unotes{color:var(--muted); font-size:12.5px; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  #upd-notes{color:var(--fg); font-size:13.5px; line-height:1.65; margin-bottom:12px;
    max-height:260px; overflow:auto; white-space:pre-wrap; background:var(--bg);
    border:1px solid var(--border); border-radius:10px; padding:12px 14px}
  .upd-keep{color:var(--muted); font-size:12px; margin-bottom:16px; display:flex; gap:7px; align-items:center}

  /* ---- Update-Animation ---- */
  #upd-progress{display:none; text-align:center; padding:4px 4px 6px}
  #upd-pop.installing #upd-info{display:none}
  #upd-pop.installing #upd-progress{display:block}
  .inst-stage{height:128px; display:grid; place-items:center; position:relative}
  .inst-logo{width:104px; height:104px; animation:l-spin 2.6s linear infinite;
    transition:opacity .35s, transform .45s}
  .inst-check{width:104px; height:104px; position:absolute; opacity:0; transform:scale(.4)}
  .inst-check circle{fill:none; stroke:var(--accent2); stroke-width:3;
    stroke-dasharray:145; stroke-dashoffset:145}
  .inst-check path{fill:none; stroke:#fff; stroke-width:4.5; stroke-linecap:round;
    stroke-linejoin:round; stroke-dasharray:40; stroke-dashoffset:40}
  #upd-pop.ready .inst-logo{opacity:0; transform:scale(.3)}
  #upd-pop.ready .inst-check{opacity:1; transform:scale(1);
    transition:opacity .3s, transform .55s cubic-bezier(.2,1.5,.4,1)}
  #upd-pop.ready .inst-check circle{animation:draw-c .5s ease forwards}
  #upd-pop.ready .inst-check path{animation:draw-p .4s .35s ease forwards}
  @keyframes draw-c{to{stroke-dashoffset:0}}
  @keyframes draw-p{to{stroke-dashoffset:0}}

  .bar{height:12px; background:var(--bg); border:1px solid var(--border); border-radius:20px;
    overflow:hidden; margin:16px 0 9px; position:relative}
  .bar-fill{height:100%; width:0%; border-radius:20px;
    background:linear-gradient(90deg,var(--accent),var(--accent2));
    box-shadow:0 0 14px var(--accent); transition:width .25s ease}
  .bar-shine{position:absolute; inset:0; border-radius:20px;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.4),transparent);
    background-size:40% 100%; background-repeat:no-repeat; animation:shine 1.1s linear infinite}
  #upd-pop.ready .bar-shine{display:none}
  @keyframes shine{from{background-position:-45% 0}to{background-position:145% 0}}
  .inst-state{font-weight:700; font-size:15.5px; margin-top:6px}
  #upd-pop.ready .inst-state{color:var(--accent2)}
  .inst-pct{color:var(--muted); font-size:13px; font-variant-numeric:tabular-nums; margin-top:3px}
  #upd-pop.ready .inst-pct{opacity:0}

  .confetti{position:absolute; inset:0; pointer-events:none; overflow:visible}
  .confetti i{position:absolute; left:50%; top:46%; width:9px; height:9px; border-radius:2px; opacity:0}
  #upd-pop.ready .confetti i{animation:cfetti .95s ease-out forwards}
  @keyframes cfetti{0%{opacity:1; transform:translate(-50%,-50%) scale(1) rotate(0)}
    100%{opacity:0; transform:translate(calc(-50% + var(--dx)),calc(-50% + var(--dy))) scale(.3) rotate(220deg)}}

  .view{flex:1; overflow:hidden; display:none; flex-direction:column; padding:14px 18px 16px}
  .view.active{display:flex}

  /* ---- Kopf ---- */
  .head{display:flex; align-items:baseline; justify-content:space-between; margin:6px 2px 12px}
  .head h1{font-size:26px; font-weight:700; letter-spacing:-.5px}
  .head h1 .g{
    background:linear-gradient(90deg,var(--accent),var(--accent2));
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }
  .count{color:var(--muted); font-size:13px}

  /* ---- Suchzeile ---- */
  .searchbar{display:flex; gap:10px; margin-bottom:12px}
  .search{
    flex:1; display:flex; align-items:center; gap:9px; background:var(--surface);
    border:1px solid var(--border); border-radius:12px; padding:0 14px; height:42px;
    transition:border-color .15s, box-shadow .15s;
  }
  .search:focus-within{border-color:var(--accent); box-shadow:0 0 0 3px rgba(236,116,86,.20)}
  .search svg{flex:none; color:var(--muted)}
  .search input{
    flex:1; background:transparent; border:none; outline:none; color:var(--fg);
    font-size:14px; font-family:inherit;
  }
  .btn{
    display:inline-flex; align-items:center; gap:8px; height:42px; padding:0 16px;
    border:none; border-radius:11px; background:var(--surface); color:var(--fg);
    font-family:inherit; font-size:13.5px; font-weight:600; cursor:pointer;
    border:1px solid var(--border); transition:background .13s, transform .05s;
  }
  .btn:hover{background:var(--surface2)}
  .btn:active{transform:translateY(1px)}
  .btn[disabled]{opacity:.4; cursor:default; pointer-events:none}
  .btn.accent{background:var(--accent); border-color:transparent; color:#fff}
  .btn.accent:hover{background:var(--accent2)}
  .btn svg{flex:none}
  .btn.mini{height:30px; padding:0 10px; font-size:12px; border-radius:8px}
  .cell.mono{font-family:Consolas,monospace; font-size:12px}

  /* ---- Tabelle ---- */
  .table{
    flex:1; min-height:140px; display:flex; flex-direction:column; background:var(--row);
    border:1px solid var(--border); border-radius:14px; overflow:hidden;
  }
  .thead{
    display:grid; grid-template-columns:var(--cols); gap:0; padding:0 6px;
    background:var(--bg); border-bottom:1px solid var(--border); flex:none;
  }
  .th{
    padding:13px 12px; font-size:11.5px; font-weight:700; color:var(--muted);
    text-transform:uppercase; letter-spacing:.6px; cursor:pointer; white-space:nowrap;
    display:flex; align-items:center; gap:5px;
  }
  .th:hover{color:var(--fg)}
  .th.num{justify-content:flex-start}
  .th .arr{font-size:10px; opacity:.9}
  .tbody{flex:1; overflow-y:auto; padding:5px}
  .row{
    display:grid; grid-template-columns:var(--cols); align-items:center;
    padding:0 6px; border-radius:10px; cursor:default; position:relative;
    transition:background .1s;
  }
  .row .cell{padding:11px 12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .row .title{font-weight:600}
  .row .dim{color:var(--muted); font-size:13px}
  .row .ic{display:inline-flex; align-items:center; gap:7px}
  .row .ic svg{flex:none; opacity:.65}
  .row:nth-child(even){background:var(--row-alt)}
  .row:hover{background:var(--surface)}
  /* Auswahl: heller Ring + schwebender Schatten -> hebt sich auf jeder Zeilenfarbe ab */
  .row.sel{background:var(--select); z-index:2;
    box-shadow:inset 0 0 0 2px rgba(255,255,255,.92), 0 6px 20px rgba(0,0,0,.55)}
  .row.sel .title{font-weight:700}
  .row.colored{margin:1px 0}
  .row.colored .dim{color:inherit; opacity:.85}
  .row.colored .ic svg{opacity:.8}

  .empty{flex:1; display:grid; place-items:center; color:var(--muted); text-align:center; padding:30px}
  .empty .big{font-size:15px; color:var(--fg); margin-bottom:8px; font-weight:600}

  /* ---- Hauptbereich: Tabelle links, Panel rechts (nur bei Auswahl) ---- */
  .main{flex:1; display:flex; gap:14px; min-height:0}
  .side{width:320px; flex:none; display:flex; flex-direction:column; gap:12px; min-height:0}
  .main:not(.show-side) .side{display:none}
  .main.show-side .side{animation:slidein .22s ease}
  @keyframes slidein{from{opacity:0; transform:translateX(22px)} to{opacity:1; transform:none}}

  /* ---- Detail + Aktionen (rechtes Panel) ---- */
  .detail{
    flex:1; min-height:90px; background:var(--surface); border:1px solid var(--border);
    border-radius:12px; padding:13px 15px; font-family:"Cascadia Code",Consolas,monospace;
    font-size:12.5px; color:var(--muted); overflow:auto;
    white-space:pre-wrap; line-height:1.7; user-select:text;
  }
  .detail b{color:var(--fg); font-weight:600}
  .actions{display:flex; flex-direction:column; gap:9px; flex:none}
  .actions .btn{width:100%; justify-content:center}
  .actions .hint{color:var(--muted); font-size:12px; text-align:center; margin-top:2px}

  /* ---- Einstellungen ---- */
  .settings{overflow-y:auto; flex:1; padding-right:6px}
  .card{
    background:var(--surface); border:1px solid var(--border); border-radius:14px;
    padding:18px 20px; margin-bottom:14px;
  }
  .card h2{font-size:15px; margin-bottom:4px}
  .card .sub{color:var(--muted); font-size:13px; margin-bottom:14px}
  /* alle Textfelder in Karten dunkel (kein weisses Standard-Feld) */
  .card input[type=text]{background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:9px; padding:9px 12px; font-family:inherit; font-size:13.5px; outline:none}
  .card input[type=text]:focus{border-color:var(--accent)}
  .field{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
  .field input[type=text]{
    flex:1; min-width:240px; background:var(--bg); border:1px solid var(--border);
    color:var(--fg); border-radius:10px; padding:11px 13px; font-family:inherit; font-size:13.5px;
    outline:none;
  }
  .field input[type=text]:focus{border-color:var(--accent)}
  .badge{padding:4px 11px; border-radius:20px; font-size:12px; font-weight:600}
  .badge.ok{background:rgba(62,207,142,.16); color:#5fe0a6}
  .badge.no{background:rgba(229,75,88,.16); color:#ff8088}
  .row2{display:flex; align-items:center; justify-content:space-between; gap:14px; padding:9px 0}
  .row2 + .row2{border-top:1px solid var(--border)}
  .row2 .lbl{font-weight:600}
  .row2 .desc{color:var(--muted); font-size:12.5px; margin-top:2px}

  /* Toggle */
  .toggle{width:46px; height:26px; border-radius:20px; background:var(--surface2);
    position:relative; cursor:pointer; flex:none; transition:background .15s; border:1px solid var(--border)}
  .toggle.on{background:var(--accent); border-color:transparent}
  .toggle::after{content:""; position:absolute; top:2px; left:2px; width:20px; height:20px;
    border-radius:50%; background:#fff; transition:left .15s}
  .toggle.on::after{left:22px}

  .swatches{display:flex; gap:9px; flex-wrap:wrap}
  .sw{width:30px; height:30px; border-radius:9px; cursor:pointer; border:2px solid transparent;
    transition:transform .08s}
  .sw:hover{transform:scale(1.1)}
  .sw.active{border-color:#fff}

  select.sel-input{
    background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:10px; padding:10px 12px; font-family:inherit; font-size:13.5px; outline:none; cursor:pointer;
  }
  .hiddenlist{list-style:none; margin-top:8px}
  .hiddenlist li{display:flex; align-items:center; gap:10px; padding:9px 12px;
    background:var(--bg); border:1px solid var(--border); border-radius:10px; margin-bottom:7px;
    font-family:Consolas,monospace; font-size:12.5px}
  .hiddenlist li .x{margin-left:auto; cursor:pointer; color:var(--muted); padding:2px 8px; border-radius:7px}
  .hiddenlist li .x:hover{background:#e54b58; color:#fff}
  .hiddenlist .none{color:var(--muted); font-family:inherit; justify-content:center}

  /* Scrollbar */
  ::-webkit-scrollbar{width:11px; height:11px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:var(--surface2); border-radius:8px; border:3px solid var(--bg)}
  ::-webkit-scrollbar-thumb:hover{background:#2c3450}

  /* Onboarding */
  .onboard{position:fixed; inset:0; z-index:100; display:none; place-items:center;
    background:radial-gradient(120% 120% at 50% 0%, #1d1612, #0d0a09)}
  .onboard.show{display:grid; animation:obfade .3s ease}
  @keyframes obfade{from{opacity:0}to{opacity:1}}
  .ob-card{width:460px; max-width:88vw; background:var(--surface); border:1px solid var(--border);
    border-radius:20px; padding:30px 32px 24px; text-align:center;
    box-shadow:0 30px 80px rgba(0,0,0,.6)}
  .ob-logo{width:72px; height:72px; margin-bottom:14px}
  .ob-step h2{font-size:23px; font-weight:700; margin-bottom:10px}
  .ob-step p{color:var(--muted); font-size:14px; line-height:1.6; margin-bottom:6px}
  .ob-swatches{display:flex; flex-wrap:wrap; gap:12px; justify-content:center; margin-top:20px}
  .ob-sw{width:42px; height:42px; border-radius:12px; cursor:pointer; border:3px solid transparent;
    transition:transform .1s}
  .ob-sw:hover{transform:scale(1.12)}
  .ob-sw.active{border-color:#fff; transform:scale(1.12)}
  .ob-line{display:flex; align-items:center; justify-content:space-between; gap:14px;
    text-align:left; background:var(--bg); border:1px solid var(--border);
    border-radius:12px; padding:14px 16px; margin-top:18px}
  .ob-lbl{font-weight:600}
  .ob-desc{color:var(--muted); font-size:12.5px; margin-top:2px}
  .ob-folder{margin-top:14px; color:var(--muted); font-size:12.5px; text-align:left;
    background:var(--bg); border:1px solid var(--border); border-radius:12px; padding:12px 16px;
    word-break:break-all}
  .ob-list{text-align:left; background:var(--bg); border:1px solid var(--border);
    border-radius:12px; padding:12px 14px; margin-top:14px; font-size:13px; max-height:260px;
    overflow-y:auto}
  .ob-list .row{display:grid; grid-template-columns:110px 1fr; gap:10px; padding:7px 4px;
    border-bottom:1px dashed var(--border)}
  .ob-list .row:last-child{border-bottom:none}
  .ob-list .k{color:var(--fg); font-weight:600}
  .ob-list .v{color:var(--muted); line-height:1.45}
  .ob-list kbd{display:inline-block; background:var(--surface2); border:1px solid var(--border);
    border-radius:5px; padding:1px 6px; font-size:11px; font-family:inherit; color:var(--fg)}
  .ob-dots{display:flex; gap:8px; justify-content:center; margin:24px 0 18px}
  .ob-dots i{width:8px; height:8px; border-radius:50%; background:var(--surface2); transition:all .2s}
  .ob-dots i.on{background:var(--accent); width:22px; border-radius:5px}
  .ob-nav{display:flex; gap:10px; justify-content:space-between}
  .ob-nav .btn{flex:1; justify-content:center}

  /* Toast (statt nativer alert-Box) */
  .toast{position:fixed; left:50%; bottom:26px; transform:translate(-50%,20px);
    background:var(--surface2); color:var(--fg); border:1px solid var(--border);
    border-radius:11px; padding:11px 18px; font-size:13.5px; font-weight:600;
    box-shadow:0 8px 30px rgba(0,0,0,.5); opacity:0; pointer-events:none;
    transition:opacity .2s, transform .2s; z-index:80; max-width:80%}
  .toast.show{opacity:1; transform:translate(-50%,0)}

  /* Popover (Farbe) */
  .overlay{position:fixed; inset:0; background:rgba(0,0,0,.5); display:none;
    align-items:center; justify-content:center; z-index:50}
  .overlay.show{display:flex}
  .pop{background:var(--surface); border:1px solid var(--border); border-radius:16px;
    padding:20px 22px; width:340px; box-shadow:0 20px 60px rgba(0,0,0,.5)}
  .pop h3{font-size:15px; margin-bottom:14px}
  .pop .swatches{margin-bottom:16px}
  .pop .actions2{display:flex; gap:9px; justify-content:flex-end}
  .modal-input{width:100%; background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:10px; padding:11px 13px; font-family:inherit; font-size:14px; outline:none; margin-bottom:16px}
  .modal-input:focus{border-color:var(--accent)}

  /* eigener Farbwaehler */
  .cpick{margin:14px 0 16px}
  .cp-sv{position:relative; width:100%; height:132px; border-radius:11px; overflow:hidden;
    cursor:crosshair; touch-action:none}
  .cp-sv-white,.cp-sv-black{position:absolute; inset:0; pointer-events:none}
  .cp-sv-white{background:linear-gradient(90deg,#fff,rgba(255,255,255,0))}
  .cp-sv-black{background:linear-gradient(0deg,#000,rgba(0,0,0,0))}
  .cp-sv-dot{position:absolute; width:15px; height:15px; border-radius:50%; border:2px solid #fff;
    box-shadow:0 0 0 1.5px rgba(0,0,0,.45); transform:translate(-50%,-50%); pointer-events:none}
  .cp-hue{-webkit-appearance:none; appearance:none; width:100%; height:14px; border-radius:8px;
    margin:14px 0 0; outline:none; cursor:pointer;
    background:linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)}
  .cp-hue::-webkit-slider-thumb{-webkit-appearance:none; width:18px; height:18px; border-radius:50%;
    background:#fff; border:2px solid rgba(0,0,0,.35); cursor:pointer; box-shadow:0 1px 4px rgba(0,0,0,.5)}
  .cp-foot{display:flex; align-items:center; gap:10px; margin-top:13px}
  .cp-prev{width:36px; height:36px; border-radius:9px; border:1px solid var(--border); flex:none}
  .cp-hex{flex:1; background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:9px; padding:9px 12px; font-family:Consolas,monospace; font-size:14px; outline:none}
  .cp-hex:focus{border-color:var(--accent)}

  /* ---- Buddy-Tab ---- */
  .buddy-hp{width:32px; height:32px; image-rendering:pixelated; image-rendering:crisp-edges;
    border-radius:6px; background:var(--surface2)}
  .ba-headline{display:flex; align-items:flex-start; gap:22px; justify-content:space-between}
  .ba-headline > div:first-child{flex:1}
  .ba-toggle{display:flex; flex-direction:column; align-items:center; gap:6px}
  .ba-toggle-lbl{font-size:12px; color:var(--muted); letter-spacing:.02em}
  .ba-vis{display:flex; flex-direction:column; gap:10px; margin-bottom:12px}
  .ba-radio{display:flex; align-items:center; gap:10px; cursor:pointer; user-select:none; font-size:14px}
  .ba-radio input{accent-color:var(--accent); width:16px; height:16px}
  .ba-radio .ba-dim{color:var(--muted); font-style:normal; font-size:12px}
  .ba-radio .ba-dim code{background:var(--bg); padding:1px 6px; border-radius:4px; border:1px solid var(--border); font-family:Consolas,monospace}
  .ba-window{display:flex; gap:9px; margin-top:6px; transition:opacity .15s}
  .ba-window.disabled{opacity:.4; pointer-events:none}
  .ba-window input{flex:1; background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:9px; padding:9px 12px; font-family:inherit; font-size:13.5px; outline:none}
  .ba-window input:focus{border-color:var(--accent)}
  .ba-hint{color:var(--muted); font-size:12px; margin-top:8px}
  .ba-slider{display:flex; flex-direction:column; gap:6px; margin:12px 0}
  .ba-slider label{font-size:13px; color:var(--muted); display:flex; justify-content:space-between}
  .ba-slider .ba-val{color:var(--fg); font-family:Consolas,monospace}
  .ba-slider input[type=range]{width:100%; accent-color:var(--accent)}
  .ba-grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(94px, 1fr)); gap:10px;
    margin-top:8px}
  .ba-cell{background:var(--bg); border:1px solid var(--border); border-radius:10px;
    padding:8px; text-align:center; cursor:pointer; transition:transform .08s, border-color .12s}
  .ba-cell:hover{transform:translateY(-1px); border-color:var(--accent)}
  .ba-cell.active{border-color:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb, var(--accent) 32%, transparent)}
  .ba-cell img{width:80px; height:80px; image-rendering:pixelated; image-rendering:crisp-edges;
    display:block; margin:0 auto; border-radius:6px; background:#14100e}
  .ba-name{font-size:11px; color:var(--muted); margin-top:6px; text-transform:lowercase; letter-spacing:.02em}
  .ba-actions{display:flex; justify-content:space-between; align-items:center; gap:16px; margin-top:14px}
  .ba-party{display:flex; align-items:center; gap:10px; font-size:13.5px; color:var(--muted)}
  .ba-wlist{overflow:auto; max-height:50vh; border:1px solid var(--border); border-radius:10px;
    background:var(--bg); margin-bottom:12px}
  .ba-wlist-row{padding:9px 12px; cursor:pointer; border-bottom:1px solid var(--border);
    font-size:13.5px; color:var(--fg); white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
  .ba-wlist-row:last-child{border-bottom:none}
  .ba-wlist-row:hover{background:var(--surface2)}
  .ba-anchor-row{margin:14px 0 8px; display:flex; align-items:center; gap:16px; flex-wrap:wrap}
  .ba-anchor-lbl{font-size:13px; color:var(--muted); min-width:100px}
  .ba-monitor-tabs{display:flex; gap:6px; flex-wrap:wrap}
  .ba-monitor-tab{background:var(--bg); border:1px solid var(--border); border-radius:7px;
    padding:6px 10px; font-size:12px; color:var(--muted); cursor:pointer; font-family:inherit;
    transition:all .1s}
  .ba-monitor-tab:hover{border-color:var(--accent); color:var(--fg)}
  .ba-monitor-tab.active{background:var(--accent); color:#fff; border-color:transparent}
  .ba-anchor-grid{display:grid; grid-template-columns:repeat(3, 26px);
    grid-template-rows:repeat(3, 22px); gap:3px}
  .ba-anchor{background:var(--bg); border:1px solid var(--border); border-radius:6px;
    cursor:pointer; padding:0; position:relative; transition:all .1s}
  .ba-anchor::after{content:""; position:absolute; width:8px; height:8px; border-radius:2px;
    background:var(--muted); top:50%; left:50%; transform:translate(-50%,-50%); transition:background .1s}
  .ba-anchor:hover{border-color:var(--accent)}
  .ba-anchor:hover::after{background:var(--accent)}
  .ba-anchor:nth-child(1)::after{left:16%; top:16%; transform:none}
  .ba-anchor:nth-child(2)::after{left:50%; top:16%; transform:translate(-50%,0)}
  .ba-anchor:nth-child(3)::after{left:auto; right:16%; top:16%; transform:none}
  .ba-anchor:nth-child(4)::after{left:16%; top:50%; transform:translate(0,-50%)}
  .ba-anchor:nth-child(5)::after{left:50%; top:50%; transform:translate(-50%,-50%)}
  .ba-anchor:nth-child(6)::after{left:auto; right:16%; top:50%; transform:translate(0,-50%)}
  .ba-anchor:nth-child(7)::after{left:16%; top:auto; bottom:16%; transform:none}
  .ba-anchor:nth-child(8)::after{left:50%; top:auto; bottom:16%; transform:translate(-50%,0)}
  .ba-anchor:nth-child(9)::after{left:auto; right:16%; top:auto; bottom:16%; transform:none}
  .ba-pos-hint{color:var(--muted); font-size:12.5px}
  .ba-pos-hint code{background:var(--bg); padding:2px 8px; border-radius:5px; border:1px solid var(--border); color:var(--fg)}
  .ba-frame-row{display:flex; align-items:center; gap:20px; margin:14px 0 6px; flex-wrap:wrap}
  .ba-frame-styles{display:flex; align-items:center; gap:8px}
  .ba-frame-lbl{font-size:13.5px; color:var(--fg); margin-right:4px}
  .ba-style{background:var(--bg); border:1px solid var(--border); color:var(--fg); border-radius:7px;
    padding:6px 12px; font-family:inherit; font-size:13px; cursor:pointer; transition:all .1s}
  .ba-style:hover{border-color:var(--accent)}
  .ba-style.active{background:var(--accent); color:#fff; border-color:transparent}
  .ba-frame-colors{display:flex; gap:6px; transition:opacity .15s}
  .ba-frame-colors.dim{opacity:.35; pointer-events:none}
  .ba-fc{width:22px; height:22px; border-radius:6px; cursor:pointer; border:2px solid transparent; transition:transform .08s}
  .ba-fc:hover{transform:scale(1.15)}
  .ba-fc.active{border-color:#fff; box-shadow:0 0 0 1px rgba(0,0,0,.4)}
  .ba-frame-label{margin:6px 0 6px; transition:opacity .15s}
  .ba-frame-label.hidden{display:none}
  .ba-frame-label label{font-size:13px; color:var(--muted); display:flex; align-items:center; gap:10px}
  .ba-frame-label input{background:var(--bg); border:1px solid var(--border); color:var(--fg);
    border-radius:8px; padding:6px 10px; font-family:Consolas,monospace; font-size:13px;
    letter-spacing:.05em; outline:none; width:160px; text-transform:uppercase}
  .ba-frame-label input:focus{border-color:var(--accent)}
</style>
</head>
<body>
<div class="app">
  <!-- Titelleiste -->
  <!-- SVG-Sprite (Logo-Definition, einmal) -->
  <svg width="0" height="0" style="position:absolute" aria-hidden="true">
    <defs>
      <radialGradient id="coral" cx="50%" cy="46%" r="65%">
        <stop offset="0%" stop-color="#F08660"/><stop offset="60%" stop-color="#EC7456"/><stop offset="100%" stop-color="#E2654A"/>
      </radialGradient>
      <polygon id="rayLong" points="0,-470 17,-426 23,-180 9,-50 -9,-50 -23,-180 -17,-426" fill="url(#coral)"/>
      <polygon id="rayShort" points="0,-432 16,-392 22,-170 9,-50 -9,-50 -22,-170 -16,-392" fill="url(#coral)"/>
      <g id="rays" transform="translate(512 512)">
        <use href="#rayLong" transform="rotate(0)"/><use href="#rayShort" transform="rotate(18)"/>
        <use href="#rayLong" transform="rotate(36)"/><use href="#rayShort" transform="rotate(54)"/>
        <use href="#rayLong" transform="rotate(72)"/><use href="#rayShort" transform="rotate(90)"/>
        <use href="#rayLong" transform="rotate(108)"/><use href="#rayShort" transform="rotate(126)"/>
        <use href="#rayLong" transform="rotate(144)"/><use href="#rayShort" transform="rotate(162)"/>
        <use href="#rayLong" transform="rotate(180)"/><use href="#rayShort" transform="rotate(198)"/>
        <use href="#rayLong" transform="rotate(216)"/><use href="#rayShort" transform="rotate(234)"/>
        <use href="#rayLong" transform="rotate(252)"/><use href="#rayShort" transform="rotate(270)"/>
        <use href="#rayLong" transform="rotate(288)"/><use href="#rayShort" transform="rotate(306)"/>
        <use href="#rayLong" transform="rotate(324)"/><use href="#rayShort" transform="rotate(342)"/>
        <circle r="170" fill="url(#coral)"/>
      </g>
    </defs>
  </svg>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-view="sessions" onclick="switchView('sessions')">Sessions</div>
    <div class="tab" data-view="buddy" onclick="switchView('buddy')">Buddy</div>
    <div class="tab" data-view="settings" onclick="switchView('settings')">Einstellungen</div>
  </div>

  <!-- Update-Hinweis (nur sichtbar wenn Update verfuegbar) -->
  <div class="updatebar" id="updatebar">
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>
    <span class="utext"></span>
    <span class="unotes"></span>
    <button class="btn accent mini" onclick="openUpdateDialog()">Details ansehen</button>
    <button class="btn mini" onclick="dismissUpdate()">Später</button>
  </div>

  <!-- Sessions -->
  <div class="view active" id="view-sessions">
    <div class="head">
      <h1 class="titlewrap"><svg class="hlogo" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
        <g class="l-spin"><use href="#rays"/></g></svg><span><span class="g">Claude</span> Sessions</span></h1>
      <div class="count" id="count"></div>
    </div>
    <div class="searchbar">
      <label class="search">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <input id="search" placeholder="Suche nach Titel, Ordner, Inhalt …" autocomplete="off">
      </label>
      <button class="btn" onclick="doRefresh(this)">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>
        Aktualisieren
      </button>
    </div>

    <div class="main">
      <div class="table">
        <div class="thead" id="thead"></div>
        <div class="tbody" id="tbody"></div>
      </div>

      <aside class="side">
        <div class="detail" id="detail">Wähle eine Session aus, um Details zu sehen.</div>
        <div class="actions">
          <button class="btn accent" id="btn-resume" disabled onclick="doResume()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5v14l12-7z"/></svg>
            In Session einsteigen
          </button>
          <button class="btn" id="btn-rename" disabled onclick="openRename()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
            Titel ändern
          </button>
          <button class="btn" id="btn-color" disabled onclick="openColor()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="10.5" r="1.2" fill="currentColor"/><circle cx="12" cy="8" r="1.2" fill="currentColor"/><circle cx="15.5" cy="10.5" r="1.2" fill="currentColor"/></svg>
            Farbe
          </button>
          <button class="btn" id="btn-copy" disabled onclick="doCopy()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
            ID kopieren
          </button>
          <span class="hint">Doppelklick = einsteigen · F2 = umbenennen</span>
        </div>
      </aside>
    </div>
  </div>

  <!-- Buddy -->
  <div class="view" id="view-buddy">
    <div class="head">
      <h1 class="titlewrap">
        <span class="hlogo" style="width:36px;height:36px;display:inline-flex;align-items:center;justify-content:center">
          <img id="buddy-heading-preview" class="buddy-hp" alt="">
        </span>
        <span><span class="g">Dein</span> Clawd-Buddy</span>
      </h1>
      <div class="count" id="buddy-status"></div>
    </div>
    <div class="settings" id="buddy-panel"></div>
  </div>

  <!-- Einstellungen -->
  <div class="view" id="view-settings">
    <div class="head"><h1>Einstellungen</h1></div>
    <div class="settings" id="settings"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<!-- Onboarding (nur beim ersten Start) -->
<div class="onboard" id="onboard">
  <div class="ob-card">
    <svg class="ob-logo" viewBox="0 0 1024 1024"><g class="l-spin"><use href="#rays"/></g></svg>

    <div class="ob-step" data-step="0">
      <h2 id="ob-title">Willkommen 👋</h2>
      <p id="ob-intro">Dein Browser für alle lokalen Claude-Code-Sessions – durchsuchen, einfärben und per Klick wieder einsteigen. Lass uns kurz einrichten – dauert nur eine Minute.</p>
    </div>

    <div class="ob-step" data-step="1" hidden>
      <h2>Wähle deine Farbe</h2>
      <p>Die Akzentfarbe der Oberfläche. Du kannst sie später jederzeit in den Einstellungen ändern.</p>
      <div class="ob-swatches" id="ob-swatches"></div>
    </div>

    <div class="ob-step" data-step="2" hidden>
      <h2>Die Spalten</h2>
      <p>Das siehst du für jede Session. Alle Spalten kannst du in den Einstellungen ein-/ausblenden und die Reihenfolge ändern.</p>
      <div class="ob-list">
        <div class="row"><div class="k">Titel</div><div class="v">Automatisch erzeugte Kurzbeschreibung der Session – oder dein selbst vergebener Name.</div></div>
        <div class="row"><div class="k">Ordner</div><div class="v">Das Arbeitsverzeichnis, in dem die Session gestartet wurde.</div></div>
        <div class="row"><div class="k">Nachrichten</div><div class="v">Anzahl ausgetauschter Nachrichten – gute Anhaltszahl für den Umfang.</div></div>
        <div class="row"><div class="k">Zuletzt aktiv</div><div class="v">Wann du zuletzt mit der Session gearbeitet hast (heute / gestern / Datum).</div></div>
        <div class="row"><div class="k">Session-ID</div><div class="v">Interne ID (standardmäßig ausgeblendet). Praktisch zum Suchen.</div></div>
        <div class="row"><div class="k">Erste Frage</div><div class="v">Deine allererste Nachricht der Session (standardmäßig ausgeblendet).</div></div>
      </div>
    </div>

    <div class="ob-step" data-step="3" hidden>
      <h2>So geht's schnell</h2>
      <p>Die wichtigsten Handgriffe – der Rest ergibt sich beim Ausprobieren.</p>
      <div class="ob-list">
        <div class="row"><div class="k">Doppelklick</div><div class="v">Öffnet die Session direkt in Claude Code – der schnellste Weg zurück in ein Gespräch.</div></div>
        <div class="row"><div class="k"><kbd>Enter</kbd></div><div class="v">Öffnet die aktuell markierte Session (wenn das Suchfeld nicht aktiv ist).</div></div>
        <div class="row"><div class="k"><kbd>F2</kbd></div><div class="v">Session umbenennen – der Titel bleibt dauerhaft dein eigener.</div></div>
        <div class="row"><div class="k">Rechtsklick</div><div class="v">Öffnet das Menü mit Farbe, Umbenennen und Ordner ausblenden.</div></div>
        <div class="row"><div class="k">Suche</div><div class="v">Filtert live nach Titel, Ordner, ID oder erster Frage – auch mit mehreren Wörtern.</div></div>
        <div class="row"><div class="k"><kbd>F11</kbd></div><div class="v">Vollbild an/aus.</div></div>
      </div>
    </div>

    <div class="ob-step" data-step="4" hidden>
      <h2>Neu: Dein Clawd-Buddy ✨</h2>
      <p>Ein winziger animierter Clawd (20×20 Pixel) schwebt auf dem Desktop und zeigt, was gerade passiert – schreibt Claude gerade Code, denkt er nach, wurde ein Limit erreicht? Standardmäßig taucht er nur auf wenn Claude Code läuft, blendet sich weich rein und wieder aus.</p>
      <div class="ob-list">
        <div class="row"><div class="k">Aktivieren</div><div class="v">Tab „Buddy" → Toggle „An". Beim ersten Mal steht er in der Bildschirmmitte.</div></div>
        <div class="row"><div class="k">Platzieren</div><div class="v">Ecken/Kanten per Schnellwahl (auf jedem Monitor) oder „Buddy platzieren…" für freies Ziehen mit Raster.</div></div>
        <div class="row"><div class="k">Aussehen</div><div class="v">Größe 40–200 px, Deckkraft, optionaler Rahmen in deiner Wunschfarbe.</div></div>
        <div class="row"><div class="k">Rechtsklick</div><div class="v">Buddy auf dem Desktop rechtsklicken blendet ihn schnell aus.</div></div>
      </div>
    </div>

    <div class="ob-step" data-step="5" hidden>
      <h2>Fast geschafft</h2>
      <div class="ob-line">
        <div><div class="ob-lbl">Heimatordner ausblenden</div>
          <div class="ob-desc">Sessions, die direkt in deinem Benutzerordner (<code>C:\Users\...</code>) laufen, verstecken. Standardmäßig aus – aktiviere es nur, wenn dich diese Sessions stören.</div></div>
        <div class="toggle" id="ob-home" onclick="obToggleHome(this)"></div>
      </div>
      <div class="ob-folder" id="ob-folder"></div>
    </div>

    <div class="ob-dots" id="ob-dots"></div>
    <div class="ob-nav">
      <button class="btn" id="ob-back" onclick="obPrev()" style="visibility:hidden">Zurück</button>
      <button class="btn accent" id="ob-next" onclick="obNext()">Weiter</button>
    </div>
  </div>
</div>

<!-- Overlays -->
<div class="overlay" id="overlay-color">
  <div class="pop">
    <h3>Farbe für diese Session</h3>
    <div class="swatches" id="color-swatches"></div>
    <div class="cpick">
      <div class="cp-sv" id="cp-sv" onpointerdown="cpDown(event)">
        <div class="cp-sv-white"></div><div class="cp-sv-black"></div>
        <div class="cp-sv-dot" id="cp-sv-dot"></div>
      </div>
      <input type="range" min="0" max="360" value="250" class="cp-hue" id="cp-hue"
             oninput="CP.h=+this.value; cpRender()">
      <div class="cp-foot">
        <span class="cp-prev" id="cp-prev"></span>
        <input class="cp-hex" id="cp-hex" maxlength="7" onchange="cpHexIn(this.value)">
      </div>
    </div>
    <div class="actions2">
      <button class="btn" onclick="setColor('')">Keine</button>
      <button class="btn" onclick="closeOverlay('overlay-color')">Abbrechen</button>
      <button class="btn accent" onclick="setColor(cpHex())">Übernehmen</button>
    </div>
  </div>
</div>

<div class="overlay" id="overlay-rename">
  <div class="pop">
    <h3>Titel ändern</h3>
    <input class="modal-input" id="rename-input" placeholder="Neuer Titel">
    <div class="actions2">
      <button class="btn" onclick="resetTitle()" title="Umbenennung rückgängig – zeigt wieder den automatisch erzeugten Titel">Standard-Titel</button>
      <button class="btn" onclick="closeOverlay('overlay-rename')">Abbrechen</button>
      <button class="btn accent" onclick="saveRename()">Speichern</button>
    </div>
  </div>
</div>

<div class="overlay" id="overlay-buddy-win">
  <div class="pop" style="width:520px; max-height:70vh; display:flex; flex-direction:column">
    <h3>Fenster auswählen</h3>
    <div class="sub" style="margin-bottom:10px">Der Buddy erscheint nur, wenn das gewählte Fenster gerade im Vordergrund ist.</div>
    <div class="ba-wlist"></div>
    <div class="actions2">
      <button class="btn" onclick="closeOverlay('overlay-buddy-win')">Abbrechen</button>
    </div>
  </div>
</div>

<div class="overlay" id="overlay-update">
  <div class="pop" id="upd-pop" style="width:460px">
    <div id="upd-info">
      <h3 id="upd-title">Update verfügbar</h3>
      <div id="upd-notes"></div>
      <div class="upd-keep">Deine Einstellungen, Farben und Titel bleiben dabei vollständig erhalten.</div>
      <div class="actions2">
        <button class="btn" onclick="closeOverlay('overlay-update')">Später</button>
        <button class="btn accent" id="upd-install" onclick="doInstall()">Jetzt installieren</button>
      </div>
    </div>
    <div id="upd-progress">
      <div class="inst-stage">
        <div class="confetti" id="confetti"></div>
        <svg class="inst-logo" viewBox="0 0 1024 1024"><use href="#rays"/></svg>
        <svg class="inst-check" viewBox="0 0 52 52"><circle cx="26" cy="26" r="23"/><path d="M15 27l7 7 15-16"/></svg>
      </div>
      <div class="inst-state" id="inst-state">Lädt herunter…</div>
      <div class="bar"><div class="bar-fill" id="bar-fill"></div><div class="bar-shine"></div></div>
      <div class="inst-pct" id="inst-pct">0%</div>
    </div>
  </div>
</div>

<script>
const COLORS = ["#4aa3ff","#3ecf8e","#ffb454","#ff6b6b","#c08cff","#ffe066","#34d6c8","#ff8fcf"];
const ALL_COLS = {
  title:   {label:"Titel",         grow:"2.6fr"},
  project: {label:"Ordner",        grow:"2fr"},
  msgs:    {label:"Nachrichten",   grow:"1.1fr", num:true},
  when:    {label:"Zuletzt aktiv", grow:"1fr"},
  id:      {label:"Session-ID",    grow:"1.7fr"},
  first:   {label:"Erste Frage",   grow:"2.4fr"},
};
const DEFAULT_ON = {title:true, project:true, msgs:true, when:true, id:false, first:false};
function normCols(){
  const saved=(STATE && STATE.settings.columns)||[];
  const order=saved.map(c=>c.key).filter(k=>ALL_COLS[k]);
  Object.keys(ALL_COLS).forEach(k=>{ if(!order.includes(k)) order.push(k); });
  return order.map(k=>{const f=saved.find(c=>c.key===k); return {key:k, on: f?!!f.on:DEFAULT_ON[k]};});
}
function visCols(){ return normCols().filter(c=>c.on).map(c=>({key:c.key, ...ALL_COLS[c.key]})); }
function applyCols(){ document.documentElement.style.setProperty('--cols', visCols().map(c=>c.grow).join(' ')); }
function cellHtml(s,key){
  switch(key){
    case 'title':   return `<div class="cell title">${esc(s.display_title)}</div>`;
    case 'project': return `<div class="cell dim">${esc(s.cwd||s.project)}</div>`;
    case 'msgs':    return `<div class="cell"><span class="ic">${SVG_MSG}${s.total_msgs} <span class="dim">(${s.user_msgs}/${s.assistant_msgs})</span></span></div>`;
    case 'when':    return `<div class="cell"><span class="ic">${SVG_CAL}${esc(s.when)}</span></div>`;
    case 'id':      return `<div class="cell dim mono">${esc(s.id)}</div>`;
    case 'first':   return `<div class="cell dim">${esc(s.first_user||'—')}</div>`;
  }
  return '<div class="cell"></div>';
}
const SVG_MSG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.4 8.4 0 0 1-12 7.6L3 21l1.9-5.6A8.4 8.4 0 1 1 21 11.5z"/></svg>';
const SVG_CAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>';

let STATE = null, sessions = [], selected = null;
let sortCol = "when", sortRev = true;
let api = window.pywebview ? window.pywebview.api : null;

function lum(hex){const h=hex.replace('#','');const r=parseInt(h.substr(0,2),16),g=parseInt(h.substr(2,2),16),b=parseInt(h.substr(4,2),16);return .299*r+.587*g+.114*b;}
function esc(s){return (s||'').replace(/[&<>"'`]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'}[c]));}

// Hintergrund-Palette aus einem Grundton ableiten (alle Dunkelstufen)
const BG_TONES=[
  {key:'warm',  name:'Warm',    base:'#4a3a30'},
  {key:'neutral',name:'Neutral', base:'#3a3a3a'},
  {key:'cool',  name:'Kühl',    base:'#333c4f'},
  {key:'ocean', name:'Ozean',   base:'#2c4452'},
  {key:'violet',name:'Violett', base:'#3f3556'},
  {key:'forest',name:'Wald',    base:'#324a3a'},
  {key:'black', name:'Schwarz', base:'#2a2a2a'},
];
function shade(base,f){const h=base.replace('#','');
  let r=parseInt(h.substr(0,2),16),g=parseInt(h.substr(2,2),16),b=parseInt(h.substr(4,2),16);
  const z=x=>('0'+Math.max(0,Math.min(255,Math.round(x*f))).toString(16)).slice(-2);
  return '#'+z(r)+z(g)+z(b);}
function applyBg(base){const S=document.documentElement.style;
  S.setProperty('--bg',      shade(base,0.30));
  S.setProperty('--row',     shade(base,0.38));
  S.setProperty('--row-alt', shade(base,0.46));
  S.setProperty('--surface', shade(base,0.50));
  S.setProperty('--surface2',shade(base,0.74));
  S.setProperty('--border',  shade(base,0.62));
  S.setProperty('--select',  shade(base,0.92));}

function applyAccent(c){document.documentElement.style.setProperty('--accent',c);
  // helle Variante
  const h=c.replace('#','');let r=parseInt(h.substr(0,2),16),g=parseInt(h.substr(2,2),16),b=parseInt(h.substr(4,2),16);
  r=Math.min(255,r+25);g=Math.min(255,g+25);b=Math.min(255,b+25);
  document.documentElement.style.setProperty('--accent2',`rgb(${r},${g},${b})`);}

let BOOTED=false;
async function boot(){
  if(BOOTED) return; BOOTED=true;
  try{
    STATE = await api.get_state();
    sortCol = STATE.settings.sort_col || "when";
    sortRev = STATE.settings.sort_rev !== false;
    applyAccent(STATE.settings.accent || "#ec7456");
    applyBg(STATE.settings.bg_base || "#4a3a30");
    ingest(STATE);
    buildSwatches();
    renderHead();
    render();
    renderSettings();
    // Onboarding zeigen bei Erstinstallation ODER wenn seit dem letzten Anzeigen
    // eine neue Onboarding-Version hinzugekommen ist (nach Update). Einstellungen
    // werden dabei nicht angetastet – die Schritte spiegeln nur die aktuellen Werte.
    if(!STATE.settings.onboarded ||
       (STATE.onboarding_version && STATE.settings.onboarded_version !== STATE.onboarding_version)){
      obShow();
    }
    checkUpdate();   // im Hintergrund, blockiert nichts
  }catch(e){
    BOOTED=false; bootTries=(bootTries||0)+1;
    const c=document.getElementById('count');
    if(bootTries<5){ if(c)c.textContent='Lädt erneut…'; setTimeout(()=>{ if(!BOOTED) boot(); }, 700); }
    else if(c){ c.textContent='Fehler beim Laden: '+e; }
  }
}
let bootTries=0;

function ingest(st){STATE=st; sessions=st.sessions||[];}

let _toastT=null;
function toast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  clearTimeout(_toastT); _toastT=setTimeout(()=>t.classList.remove('show'), 2600);
}

function visible(){
  const q=document.getElementById('search').value.toLowerCase().trim();
  const hideHome=STATE.settings.hide_home, home=(STATE.home||'').toLowerCase();
  const hidden=(STATE.settings.hidden_folders||[]).map(f=>(f||'').toLowerCase());
  let rows=sessions.filter(s=>{
    const cwd=(s.cwd||'').toLowerCase();
    if(hideHome && cwd===home) return false;
    if(hidden.some(h=>cwd===h)) return false;
    if(q){const hay=((s.display_title||'')+' '+(s.project||'')+' '+(s.cwd||'')+' '+(s.first_user||'')+' '+(s.id||'')).toLowerCase();
      if(!hay.includes(q)) return false;}
    return true;
  });
  const key={title:s=>s.display_title.toLowerCase(),project:s=>(s.cwd||s.project).toLowerCase(),
    msgs:s=>s.total_msgs,when:s=>s.mtime,id:s=>s.id.toLowerCase(),
    first:s=>(s.first_user||'').toLowerCase()}[sortCol] || (s=>s.mtime);
  rows.sort((a,b)=>{const x=key(a),y=key(b);return (x<y?-1:x>y?1:0)*(sortRev?-1:1);});
  return rows;
}

function renderHead(){
  applyCols();
  document.getElementById('thead').innerHTML = visCols().map(c=>{
    const arr = c.key===sortCol ? `<span class="arr">${sortRev?'▼':'▲'}</span>`:'';
    return `<div class="th ${c.num?'num':''}" onclick="sortBy('${c.key}')">${c.label}${arr}</div>`;
  }).join('');
}

function render(){
  const rows=visible();
  const tb=document.getElementById('tbody');
  if(!STATE.found){
    tb.innerHTML=`<div class="empty"><div><div class="big">Kein Sessions-Ordner gefunden</div>
      Lege ihn unter „Einstellungen“ fest.</div></div>`;
    document.getElementById('count').textContent='';
    return;
  }
  // Auswahl loeschen, wenn die Zeile (durch Suche/Filter) nicht mehr sichtbar ist
  if(selected && !rows.some(s=>s.id===selected)) selected=null;
  if(rows.length===0){
    tb.innerHTML=`<div class="empty"><div><div class="big">Keine Sessions</div>Nichts gefunden.</div></div>`;
  } else {
    tb.innerHTML=rows.map(s=>{
      const col=s.color;
      let style='', cls='row';
      if(col){const tc=lum(col)>150?'#10101a':'#ffffff';
        style=`style="background:${col};color:${tc}"`; cls+=' colored';}
      if(selected===s.id) cls+=' sel';
      const cells=visCols().map(c=>cellHtml(s,c.key)).join('');
      return `<div class="${cls}" ${style} data-id="${s.id}"
        onclick="selectRow('${s.id}')" ondblclick="doResumeRow('${s.id}')">${cells}</div>`;
    }).join('');
  }
  const total=sessions.length, q=document.getElementById('search').value.trim();
  let txt = q ? `${rows.length} Treffer` : `${rows.length} Sessions`;
  document.getElementById('count').textContent=txt;
  updateDetail();   // Panel/Buttons immer synchron zur Auswahl halten
}

function selectRow(id){ selected = (selected===id ? null : id); render(); }   // erneuter Klick = abwählen
function getSel(){return sessions.find(s=>s.id===selected);}

function updateDetail(){
  const s=getSel();
  const en=!!s;
  // rechtes Panel nur zeigen, wenn etwas ausgewaehlt ist
  document.querySelector('.main').classList.toggle('show-side', en);
  ['btn-resume','btn-rename','btn-color','btn-copy'].forEach(b=>document.getElementById(b).disabled=!en);
  const d=document.getElementById('detail');
  if(!s){d.textContent='Wähle eine Session aus, um Details zu sehen.';return;}
  const start=(s.first_user||'—').replace(/\s+/g,' ').trim().slice(0,260);
  d.innerHTML=`<b>ID</b>      ${esc(s.id)}\n<b>Ordner</b>  ${esc(s.cwd||'(unbekannt)')}\n`
    +`<b>Verlauf</b> ${s.user_msgs} von dir · ${s.assistant_msgs} von Claude\n`
    +`<b>Start</b>   ${esc(start)}`;
}

function sortBy(c){
  if(sortCol===c) sortRev=!sortRev;
  else {sortCol=c; sortRev=(c==='msgs'||c==='when');}
  api.update_setting('sort_col',sortCol);
  api.update_setting('sort_rev',sortRev);
  renderHead(); render();
}

let BUDDY_STATUS_TIMER = null;
function switchView(v){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===v));
  document.getElementById('view-sessions').classList.toggle('active',v==='sessions');
  document.getElementById('view-settings').classList.toggle('active',v==='settings');
  document.getElementById('view-buddy').classList.toggle('active',v==='buddy');
  if(v==='buddy'){
    renderBuddy();
    if(!BUDDY_STATUS_TIMER) BUDDY_STATUS_TIMER = setInterval(refreshBuddyStatus, 2500);
  } else if(BUDDY_STATUS_TIMER){
    clearInterval(BUDDY_STATUS_TIMER); BUDDY_STATUS_TIMER = null;
  }
  try{ api.buddy_notify_view(v); }catch(_){}
}
async function refreshBuddyStatus(){
  try{
    const d = await api.buddy_state();
    if(!d) return;
    const b = d.config || {};
    let s;
    if (!d.have_sprites) s = 'Sprite-Daten fehlen – bitte neu installieren.';
    else if (!b.enabled) s = 'Buddy aus';
    else if (!d.running) s = 'Startet…';
    else if (d.reason) s = 'Buddy läuft · ' + d.reason;
    else s = 'Buddy läuft';
    const el = document.getElementById('buddy-status');
    if(el) el.textContent = s;
  }catch(_){}
}

async function doRefresh(btn){if(btn)btn.disabled=true; ingest(await api.refresh()); render(); updateDetail(); if(btn)btn.disabled=false;}
async function doResume(){const s=getSel(); if(!s)return; await api.resume(s.id,s.cwd,s.project||'');}
async function doResumeRow(id){const s=sessions.find(x=>x.id===id); if(!s)return; selected=id; render(); await api.resume(s.id,s.cwd,s.project||'');}
async function doCopy(){const s=getSel(); if(!s)return; await api.copy(s.id); toast('Session-ID kopiert ✓');}

/* ---- Farbe ---- */
function buildSwatches(){
  const html=COLORS.map(c=>`<div class="sw" style="background:${c}" onclick="setColor('${c}')"></div>`).join('');
  document.getElementById('color-swatches').innerHTML=html;
}
function openColor(){const s=getSel(); if(!s)return;
  const start=s.color||STATE.settings.accent||'#6c6cff';
  const v=hex2hsv(start); CP.h=v.h; CP.s=v.s||1; CP.v=(v.v===undefined?1:v.v); cpRender();
  document.getElementById('overlay-color').classList.add('show');}
async function setColor(c){const s=getSel(); if(!s){closeOverlay('overlay-color');return;}
  ingest(await api.set_color(s.id,c)); render(); updateDetail(); closeOverlay('overlay-color');}

/* eigener Farbwaehler (HSV) */
let CP={h:250,s:1,v:1}, CPdrag=false;
function hsv2hex(h,s,v){h/=360;let i=Math.floor(h*6),f=h*6-i,p=v*(1-s),q=v*(1-f*s),t=v*(1-(1-f)*s),r,g,b;
  switch(i%6){case 0:r=v,g=t,b=p;break;case 1:r=q,g=v,b=p;break;case 2:r=p,g=v,b=t;break;
    case 3:r=p,g=q,b=v;break;case 4:r=t,g=p,b=v;break;default:r=v,g=p,b=q;}
  const z=x=>('0'+Math.round(x*255).toString(16)).slice(-2); return '#'+z(r)+z(g)+z(b);}
function hex2hsv(hex){hex=(hex||'').replace('#',''); if(hex.length===3)hex=hex.split('').map(c=>c+c).join('');
  let r=parseInt(hex.substr(0,2),16)/255,g=parseInt(hex.substr(2,2),16)/255,b=parseInt(hex.substr(4,2),16)/255;
  if(isNaN(r))return{h:250,s:1,v:1};
  let mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn,h=0;
  if(d){if(mx===r)h=((g-b)/d+6)%6;else if(mx===g)h=(b-r)/d+2;else h=(r-g)/d+4;h*=60;}
  return {h:h, s:mx?d/mx:0, v:mx};}
function cpHex(){return hsv2hex(CP.h,CP.s,CP.v);}
function cpRender(){
  const sv=document.getElementById('cp-sv'); if(!sv)return;
  sv.style.background='hsl('+CP.h+',100%,50%)';
  const dot=document.getElementById('cp-sv-dot');
  dot.style.left=(CP.s*100)+'%'; dot.style.top=((1-CP.v)*100)+'%';
  const hex=cpHex();
  document.getElementById('cp-prev').style.background=hex;
  document.getElementById('cp-hex').value=hex;
  document.getElementById('cp-hue').value=CP.h;
}
function cpPick(e){const sv=document.getElementById('cp-sv'),r=sv.getBoundingClientRect();
  CP.s=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
  CP.v=Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height)); cpRender();}
function cpDown(e){CPdrag=true; try{e.target.setPointerCapture(e.pointerId);}catch(_){} cpPick(e);}
function cpHexIn(v){const x=hex2hsv(v); CP.h=x.h; CP.s=x.s; CP.v=x.v; cpRender();}

/* ---- Umbenennen ---- */
function openRename(){const s=getSel(); if(!s)return;
  const i=document.getElementById('rename-input'); i.value=s.display_title;
  document.getElementById('overlay-rename').classList.add('show'); setTimeout(()=>{i.focus();i.select();},50);}
async function saveRename(){const s=getSel(); if(!s)return;
  ingest(await api.rename(s.id,document.getElementById('rename-input').value)); render(); updateDetail(); closeOverlay('overlay-rename');}
async function resetTitle(){
  const s=getSel(); if(!s) return;
  ingest(await api.rename(s.id,''));   // leerer Titel = Override loeschen -> Auto-Titel
  render(); updateDetail(); closeOverlay('overlay-rename');
  toast('Standard-Titel wiederhergestellt');
}
function closeOverlay(id){document.getElementById(id).classList.remove('show');}

/* ---- Buddy ---- */
let BUDDY=null;   // wird beim ersten Oeffnen geladen
let BUDDY_PREVIEWS={};  // {animName: dataURL} – Cache damit Vorschauen beim Rerender nicht flackern
let BUDDY_MON_CACHE=[]; // Monitor-Liste zwischen Rerenders halten – verhindert Layout-Sprung
async function renderBuddy(){
  const data = await api.buddy_state();
  BUDDY = data;
  const b = data.config || {};
  const anims = data.anims || [];
  const previewName = data.preview_name || 'idle breathe';
  const previewSrc = data.preview || '';
  // Kopf-Vorschau (Miniatur im Titel)
  const hp = document.getElementById('buddy-heading-preview');
  if (hp && previewSrc) hp.src = previewSrc;

  let statusTxt;
  if (!data.have_sprites) statusTxt = 'Sprite-Daten fehlen – bitte neu installieren.';
  else if (!b.enabled) statusTxt = 'Buddy aus';
  else if (!data.running) statusTxt = 'Startet…';
  else if (data.reason) statusTxt = 'Buddy läuft · ' + data.reason;
  else statusTxt = 'Buddy läuft';
  document.getElementById('buddy-status').textContent = statusTxt;

  const size = Math.max(2, Math.min(10, +b.size||4));
  const opacity = Math.max(20, Math.min(100, +b.opacity||100));
  const vis = b.visibility || 'always';
  const target = b.target_window || '';

  // Animations-Vorschau-Grid (Klick = kurz auf dem echten Buddy abspielen)
  const previewList = anims.map(a=>{
    const cached = BUDDY_PREVIEWS[a.name] || '';
    const srcAttr = cached ? `src="${cached}"` : '';
    return `<div class="ba-cell" title="${esc(a.name)} · ${a.frames} Frames · Klick zum Vorspielen" onclick="buddyPickAnim('${esc(a.name)}', this)">
      <img data-anim="${esc(a.name)}" ${srcAttr} alt="${esc(a.name)}">
      <div class="ba-name">${esc(a.name)}</div>
    </div>`;
  }).join('');

  document.getElementById('buddy-panel').innerHTML=`
    <div class="card">
      <div class="ba-headline">
        <div>
          <h2>Dein kleiner Buddy auf dem Desktop</h2>
          <div class="sub">Ein winziger animierter Clawd (20×20 Pixel) schwebt auf dem Desktop – frameless, immer im Vordergrund. Zieh ihn mit der Maus wohin du magst. Rechtsklick auf ihn blendet ihn aus.</div>
        </div>
        <div class="ba-toggle">
          <div class="toggle ${b.enabled?'on':''}" onclick="buddyToggle()"></div>
          <div class="ba-toggle-lbl">${b.enabled?'An':'Aus'}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Wann sichtbar</h2>
      <div class="sub">Der Buddy kann immer da sein oder nur wenn ein bestimmtes Programm gerade im Vordergrund ist – z.B. nur wenn Claude Code im Terminal läuft.</div>
      <div class="ba-vis">
        <label class="ba-radio"><input type="radio" name="ba-vis" ${vis==='when_claude'?'checked':''} onchange="buddySet('visibility','when_claude')"> <span>Nur wenn Claude Code läuft <em class="ba-dim">(erkennt Terminal + <code>claude.exe</code>)</em></span></label>
        <label class="ba-radio"><input type="radio" name="ba-vis" ${vis==='always'?'checked':''} onchange="buddySet('visibility','always')"> <span>Immer sichtbar</span></label>
        <label class="ba-radio"><input type="radio" name="ba-vis" ${vis==='when_window'?'checked':''} onchange="buddySet('visibility','when_window')"> <span>Nur wenn dieses Fenster vorne ist:</span></label>
      </div>
      <div class="ba-window ${vis==='when_window'?'':'disabled'}">
        <input type="text" id="ba-target" placeholder="z.B. „claude" oder Titel-Ausschnitt" value="${esc(target)}"
               onchange="buddySet('target_window', this.value)">
        <button class="btn" onclick="buddyPickWindow()">Aus offenen Fenstern wählen…</button>
      </div>
      <div class="ba-hint">Passt zu jedem Fenster, dessen Titel den eingegebenen Text enthält (Groß-/Kleinschreibung egal).</div>
    </div>

    <div class="card">
      <h2>Aussehen & Position</h2>
      <div class="sub">Größe und Deckkraft ändern sich sofort. Für die Position wähle eine Ecke oder Kante – oder ziehe den Buddy per „Platzieren" frei hin (Bewegung rastet aufs Raster und schnappt am Bildschirmrand).</div>

      <div class="ba-slider">
        <label>Größe <span class="ba-val" id="ba-size-val">${size*20} px</span></label>
        <input type="range" min="2" max="10" step="1" value="${size}" oninput="buddyLive('size', +this.value)" onchange="buddySet('size', +this.value)">
      </div>
      <div class="ba-slider">
        <label>Deckkraft <span class="ba-val" id="ba-op-val">${opacity} %</span></label>
        <input type="range" min="20" max="100" step="5" value="${opacity}" oninput="buddyLive('opacity', +this.value)" onchange="buddySet('opacity', +this.value)">
      </div>

      <div class="ba-frame-row">
        <div class="ba-frame-styles">
          <span class="ba-frame-lbl">Rahmen</span>
          ${[
            ['off','Aus'],
            ['classic','Cam'],
          ].map(([v,l])=>{
            const cur = (b.frame_style==='webcam'?'classic':(b.frame_style||'off'));
            const active = (cur===v)?'active':'';
            return `<button class="ba-style ${active}" onclick="buddySet('frame_style','${v}')">${l}</button>`;
          }).join('')}
        </div>
        <div class="ba-frame-colors ${(!b.frame_style || b.frame_style==='off')?'dim':''}">
          ${['#ec7456','#6c6cff','#3ecf8e','#4aa3ff','#ffb454','#ff6b6b','#c08cff','#34d6c8','#ffffff']
             .map(c=>`<div class="ba-fc ${b.frame_color===c?'active':''}" style="background:${c}" onclick="buddySet('frame_color','${c}')"></div>`).join('')}
        </div>
      </div>
      <div class="ba-frame-label ${(b.frame_style==='classic'||b.frame_style==='webcam')?'':'hidden'}">
        <label>Cam-Name <input type="text" maxlength="7" value="${esc(b.frame_label||'CLAWD')}" onchange="buddySet('frame_label', this.value)"></label>
      </div>

      <div class="ba-anchor-row">
        <div class="ba-anchor-lbl">Schnellwahl</div>
        <div class="ba-monitor-tabs" id="ba-mon-tabs"></div>
        <div class="ba-anchor-grid">
          <button class="ba-anchor" title="Oben links"   onclick="buddyAnchor('tl')"></button>
          <button class="ba-anchor" title="Oben Mitte"   onclick="buddyAnchor('tc')"></button>
          <button class="ba-anchor" title="Oben rechts"  onclick="buddyAnchor('tr')"></button>
          <button class="ba-anchor" title="Mitte links"  onclick="buddyAnchor('ml')"></button>
          <button class="ba-anchor" title="Mitte"        onclick="buddyAnchor('c')"></button>
          <button class="ba-anchor" title="Mitte rechts" onclick="buddyAnchor('mr')"></button>
          <button class="ba-anchor" title="Unten links"  onclick="buddyAnchor('bl')"></button>
          <button class="ba-anchor" title="Unten Mitte"  onclick="buddyAnchor('bc')"></button>
          <button class="ba-anchor" title="Unten rechts" onclick="buddyAnchor('br')"></button>
        </div>
      </div>

      <div class="ba-actions">
        <div class="ba-pos-hint">Aktuell bei <code>${b.x||200}, ${b.y||200}</code></div>
        <button class="btn accent" onclick="buddyPlace()">Buddy platzieren…</button>
      </div>
    </div>

    <div class="card">
      <h2>Animationen ausprobieren</h2>
      <div class="sub">Normalerweise wählt der Buddy die Animation automatisch nach dem, was in deinen Sessions passiert. Klick eine Animation an, um sie kurz auf dem echten Buddy vorzuspielen.</div>
      <div class="ba-grid">${previewList}</div>
      <div class="ba-actions">
        <button class="btn accent" onclick="buddySurprise()">Kurz „Überraschung" zeigen</button>
        <div class="ba-party">
          <span>Party-Modus (nur Tanz)</span>
          <div class="toggle ${b.party?'on':''}" onclick="buddySetToggle('party')"></div>
        </div>
      </div>
    </div>
  `;

  // BMP-Previews fuer alle Anims nachladen (nur wenn nicht im Cache)
  document.querySelectorAll('#buddy-panel img[data-anim]').forEach(async img=>{
    const n = img.dataset.anim;
    if(img.src) return;   // schon aus Cache befuellt
    const src = await api.buddy_preview(n);
    BUDDY_PREVIEWS[n] = src;
    img.src = src;
  });
  // Monitor-Tabs sofort aus Cache rendern damit kein Layout-Sprung entsteht
  if(BUDDY_MON_CACHE.length){ renderMonitorTabs(BUDDY_MON_CACHE); }
  buddyLoadMonitors();
}
function renderMonitorTabs(mons){
  const box = document.getElementById('ba-mon-tabs');
  if(!box) return;
  if(!mons || !mons.length){ box.innerHTML=''; return; }
  if(BUDDY_MON_IDX!==null && BUDDY_MON_IDX >= mons.length) BUDDY_MON_IDX=null;
  const tabs = mons.map(m=>{
    const active = (BUDDY_MON_IDX===m.idx)?'active':'';
    return `<button class="ba-monitor-tab ${active}" onclick="buddyPickMonitor(${m.idx})">${esc(m.label)}</button>`;
  }).join('');
  const auto = (BUDDY_MON_IDX===null)?'active':'';
  box.innerHTML = `<button class="ba-monitor-tab ${auto}" onclick="buddyPickMonitor(null)" title="Ecke/Kante auf dem Monitor unter dem Buddy">aktuell</button>` + tabs;
}

async function buddyToggle(){
  const b = (BUDDY&&BUDDY.config)||{};
  const next = !b.enabled;
  await api.buddy_set('enabled', next);
  await renderBuddy();
  toast(next ? 'Buddy an ✓' : 'Buddy aus');
}
async function buddySet(key, value){
  await api.buddy_set(key, value);
  renderBuddy();
}
async function buddySetToggle(key){
  const b=(BUDDY&&BUDDY.config)||{};
  await api.buddy_set(key, !b[key]);
  renderBuddy();
}
function buddyLive(key, value){
  const v = document.getElementById(key==='size'?'ba-size-val':'ba-op-val');
  if(v) v.textContent = (key==='size') ? (value*20)+' px' : value+' %';
}
async function buddySurprise(){
  await api.buddy_surprise();
  toast('Buddy: Überraschung!');
}
async function buddyPlace(){
  await api.buddy_place();
}
async function buddyPickAnim(name, cell){
  // Kurz-Feedback im Grid + Buddy spielt die Anim 3.5 s auf dem Desktop
  document.querySelectorAll('#buddy-panel .ba-cell').forEach(el=>el.classList.remove('active'));
  if(cell) cell.classList.add('active');
  setTimeout(()=>{ if(cell) cell.classList.remove('active'); }, 3600);
  const hp = document.getElementById('buddy-heading-preview');
  if(hp){ hp.src = await api.buddy_preview(name); }
  await api.buddy_preview_anim(name);
  toast('Buddy zeigt: ' + name);
}
let BUDDY_MON_IDX = null;   // Auswahl im Monitor-Picker (null = aktueller unter Buddy)
async function buddyAnchor(pos){
  const st = await api.buddy_anchor(pos, BUDDY_MON_IDX);
  BUDDY = st;
  renderBuddy();
}
async function buddyLoadMonitors(){
  const mons = await api.buddy_monitors();
  BUDDY_MON_CACHE = mons || [];
  renderMonitorTabs(BUDDY_MON_CACHE);
}
function buddyPickMonitor(idx){
  BUDDY_MON_IDX = idx;
  renderMonitorTabs(BUDDY_MON_CACHE);
}
async function buddyPickWindow(){
  const list = await api.buddy_windows();
  if(!list || !list.length){ toast('Keine Fenster gefunden.'); return; }
  // Simples Overlay-Menue
  const html = list.map(t=>`<div class="ba-wlist-row" onclick="buddyChoseWindow(this.dataset.t)" data-t="${esc(t)}">${esc(t)}</div>`).join('');
  const box = document.getElementById('overlay-buddy-win');
  box.querySelector('.ba-wlist').innerHTML = html;
  box.classList.add('show');
}
async function buddyChoseWindow(title){
  document.getElementById('ba-target').value = title;
  closeOverlay('overlay-buddy-win');
  await api.buddy_set('target_window', title);
  await api.buddy_set('visibility', 'when_window');
  renderBuddy();
}

/* ---- Einstellungen ---- */
function renderSettings(){
  const st=STATE.settings, found=STATE.found, pdir=STATE.projects_dir||'(nicht gesetzt)';
  const hidden=st.hidden_folders||[];
  const hl = hidden.length ? hidden.map((f,i)=>`<li>${esc(f)}<span class="x" onclick="unhideIdx(${i})">✕</span></li>`).join('')
    : '<li class="none">Keine</li>';
  const ACCENTS = ['#ec7456','#6c6cff','#3ecf8e','#4aa3ff','#ffb454','#ff6b6b','#c08cff','#34d6c8'];
  const swl = ACCENTS.map(c=>`<div class="sw ${st.accent===c?'active':''}" style="background:${c}" onclick="setAccent('${c}')"></div>`).join('');
  const bgl = BG_TONES.map(t=>`<div class="sw ${st.bg_base===t.base?'active':''}" style="background:${shade(t.base,0.42)}" title="${t.name}" onclick="setBg('${t.base}')"></div>`).join('');
  document.getElementById('settings').innerHTML=`
    <div class="card">
      <h2>Sessions-Ordner</h2>
      <div class="sub">Wo Claude die Session-Dateien speichert. Wird automatisch gesucht, lässt sich aber überschreiben.</div>
      <div class="field">
        <input type="text" id="pdir" value="${esc(pdir)}" readonly>
        <span class="badge ${found?'ok':'no'}">${found?'Gefunden':'Nicht gefunden'}</span>
        <button class="btn" onclick="browseFolder()">Durchsuchen…</button>
        <button class="btn" onclick="autoDetect()">Auto</button>
      </div>
    </div>

    <div class="card">
      <h2>Anzeige</h2>
      <div class="row2">
        <div><div class="lbl">Heimatordner ausblenden</div>
          <div class="desc">Sessions direkt in ${esc(STATE.home)} verstecken (Unterordner bleiben sichtbar).</div></div>
        <div class="toggle ${st.hide_home?'on':''}" onclick="toggleHome(this)"></div>
      </div>
    </div>

    <div class="card">
      <h2>Fenster schließen</h2>
      <div class="row2">
        <div><div class="lbl">Im Hintergrund weiterlaufen</div>
          <div class="desc">Wenn aktiv, versteckt der X-Button die App nur (Icon im System-Tray unten rechts, Klick öffnet sie wieder). Ausschalten wenn X wirklich beenden soll.</div></div>
        <div class="toggle ${st.close_to_tray!==false?'on':''}" onclick="toggleTray(this)"></div>
      </div>
      <button class="btn" onclick="reallyQuit()" style="margin-top:12px">App jetzt komplett beenden</button>
    </div>

    <div class="card">
      <h2>Autostart</h2>
      <div class="row2">
        <div><div class="lbl">Mit Windows starten</div>
          <div class="desc">Die App startet automatisch nach dem Anmelden – praktisch damit der Buddy und der Tray-Modus sofort verfügbar sind. Registry-Eintrag unter HKCU\\Run.</div></div>
        <div class="toggle ${st.autostart!==false?'on':''}" onclick="toggleAutostart(this)"></div>
      </div>
    </div>

    <div class="card">
      <h2>Benachrichtigungen</h2>
      <div class="row2">
        <div><div class="lbl">Bei Limit-Reset benachrichtigen</div>
          <div class="desc">Windows-Systembenachrichtigung wenn dein Claude-Limit sich zurückgesetzt hat und du wieder loslegen kannst. Braucht den System-Tray aktiv.</div></div>
        <div class="toggle ${st.notify_limit_reset!==false?'on':''}" onclick="toggleLimitNotif(this)"></div>
      </div>
    </div>

    <div class="card">
      <h2>Weitere ausgeblendete Ordner</h2>
      <div class="sub">Sessions in diesen Ordnern werden komplett ausgeblendet.</div>
      <ul class="hiddenlist">${hl}</ul>
      <button class="btn" onclick="hideCurrent()" style="margin-top:6px">+ Ordner der gewählten Session ausblenden</button>
    </div>

    <div class="card">
      <h2>Akzentfarbe</h2>
      <div class="sub">Farbe für Buttons, Auswahl und Hervorhebungen.</div>
      <div class="swatches">${swl}</div>
    </div>

    <div class="card">
      <h2>Hintergrund</h2>
      <div class="sub">Grundton der Oberfläche – Flächen, Zeilen und Ränder werden daraus abgeleitet.</div>
      <div class="swatches">${bgl}</div>
    </div>

    <div class="card">
      <h2>Spalten</h2>
      <div class="sub">Welche Spalten in der Tabelle erscheinen und in welcher Reihenfolge.</div>
      ${normCols().map((c,i,arr)=>`
        <div class="row2">
          <div class="lbl">${ALL_COLS[c.key].label}</div>
          <div style="display:flex; gap:6px; align-items:center">
            <button class="btn mini" onclick="moveCol(${i},-1)" ${i===0?'disabled':''}>▲</button>
            <button class="btn mini" onclick="moveCol(${i},1)" ${i===arr.length-1?'disabled':''}>▼</button>
            <div class="toggle ${c.on?'on':''}" onclick="toggleCol('${c.key}')"></div>
          </div>
        </div>`).join('')}
    </div>

    <div class="card">
      <h2>Terminal & Claude</h2>
      <div class="row2">
        <div><div class="lbl">Womit öffnen?</div><div class="desc">Wie eine Session gestartet wird.</div></div>
        <select class="sel-input" onchange="api.update_setting('terminal',this.value)">
          <option value="auto" ${st.terminal==='auto'?'selected':''}>Automatisch</option>
          <option value="wt" ${st.terminal==='wt'?'selected':''}>Windows Terminal</option>
          <option value="cmd" ${st.terminal==='cmd'?'selected':''}>Eingabeaufforderung (cmd)</option>
        </select>
      </div>
      <div class="row2">
        <div><div class="lbl">Claude-Befehl</div><div class="desc">Pfad/Name der Claude-CLI (Standard: claude).</div></div>
        <input type="text" style="max-width:260px" value="${esc(st.claude_cmd||'claude')}"
          onchange="api.update_setting('claude_cmd',this.value)">
      </div>
    </div>

    <div class="card">
      <h2>Updates</h2>
      <div class="sub">Aktuelle Version: v${esc(STATE.version||'?')} — beim Start wird automatisch nach Updates gesucht (ohne Internet wird das übersprungen).</div>
      <div class="field">
        <button class="btn" onclick="manualCheck(this)">Nach Updates suchen</button>
        <span id="upd-status" class="badge"></span>
      </div>
    </div>
  `;
}

async function toggleTray(el){
  const on=!el.classList.contains('on'); el.classList.toggle('on',on);
  ingest(await api.update_setting('close_to_tray', on));
  await api.buddy_apply_tray(on);
}
async function toggleLimitNotif(el){
  const on=!el.classList.contains('on'); el.classList.toggle('on',on);
  ingest(await api.update_setting('notify_limit_reset', on));
  toast(on?'Limit-Benachrichtigung an ✓':'Limit-Benachrichtigung aus');
}
async function toggleAutostart(el){
  const on=!el.classList.contains('on'); el.classList.toggle('on',on);
  const r = await api.set_autostart(on);
  ingest(await api.get_state());
  if(!r || !r.ok) toast('Autostart konnte nicht gesetzt werden');
  else toast(on?'Autostart an ✓':'Autostart aus');
}
async function reallyQuit(){
  await api.buddy_real_quit();
}
async function browseFolder(){ingest(await api.browse_folder()); render(); renderSettings();}
async function autoDetect(){ingest(await api.update_setting('projects_dir','')); render(); renderSettings();}
async function toggleHome(el){const on=!el.classList.contains('on');
  ingest(await api.update_setting('hide_home',on)); render(); renderSettings();}
async function unhideIdx(i){const f=(STATE.settings.hidden_folders||[])[i]; if(f===undefined)return;
  ingest(await api.remove_hidden_folder(f)); render(); renderSettings();}
async function hideCurrent(){const s=getSel();
  if(!s||!s.cwd){toast('Erst im Tab „Sessions" eine Session auswählen.');return;}
  ingest(await api.add_hidden_folder(s.cwd)); render(); renderSettings(); }
async function setAccent(c){applyAccent(c); ingest(await api.update_setting('accent',c)); renderSettings();}
async function setBg(base){applyBg(base); ingest(await api.update_setting('bg_base',base)); renderSettings();}

async function persistCols(arr){ ingest(await api.update_setting('columns',arr)); renderHead(); render(); renderSettings(); }
function toggleCol(key){ const cols=normCols(); const t=cols.find(c=>c.key===key);
  if(t.on && cols.filter(c=>c.on).length<=1) return;  // mind. eine Spalte sichtbar lassen
  t.on=!t.on; persistCols(cols); }
function moveCol(i,dir){ const cols=normCols(); const j=i+dir; if(j<0||j>=cols.length) return;
  const t=cols[i]; cols[i]=cols[j]; cols[j]=t; persistCols(cols); }

/* ---- Update ---- */
let UPD=null;
function showUpdateBar(u){
  UPD=u;
  const bar=document.getElementById('updatebar');
  bar.querySelector('.utext').textContent='Update verfügbar: v'+u.latest;
  bar.querySelector('.unotes').textContent=u.notes? ('— '+u.notes) : '';
  bar.classList.add('show');
}
async function checkUpdate(){
  try{
    // Falls das letzte Update-Batch nicht durchkam, informieren.
    if(await api.consume_update_failed_marker()){
      toast('Update konnte nicht übernommen werden – bitte manuell installieren');
    }
    const u=await api.check_update();
    if(u&&u.available) showUpdateBar(u);
  }catch(_){}
}
function openUpdateDialog(){
  if(!UPD) return;
  document.getElementById('upd-title').textContent='Update auf v'+UPD.latest+' (aktuell v'+UPD.current+')';
  document.getElementById('upd-notes').textContent=UPD.notes||'Verbesserungen und Fehlerbehebungen.';
  const b=document.getElementById('upd-install');
  b.disabled=false; b.textContent= UPD.frozen ? 'Jetzt installieren' : 'Zur Download-Seite';
  document.getElementById('overlay-update').classList.add('show');
}
function buildConfetti(){
  const cols=['#ec7456','#f5926f','#4aa3ff','#3ecf8e','#ffe066','#c08cff','#34d6c8'];
  let h='';
  for(let i=0;i<18;i++){
    const a=(i/18)*6.2832, r=80+(i%3)*26;
    const dx=Math.cos(a)*r, dy=Math.sin(a)*r-18;
    h+=`<i style="--dx:${dx.toFixed(0)}px;--dy:${dy.toFixed(0)}px;background:${cols[i%cols.length]}"></i>`;
  }
  document.getElementById('confetti').innerHTML=h;
}
function setProgress(p){
  p=Math.max(0,Math.min(100, p|0));
  document.getElementById('bar-fill').style.width=p+'%';
  document.getElementById('inst-pct').textContent=p+'%';
}
function startInstallUI(){
  const pop=document.getElementById('upd-pop');
  pop.classList.add('installing'); pop.classList.remove('ready');
  setProgress(0); document.getElementById('inst-state').textContent='Lädt herunter…';
  buildConfetti();
  document.getElementById('overlay-update').classList.add('show');
}
// von Python aufgerufen
window.updateProgress=function(p){
  setProgress(p);
  if(p>=100) document.getElementById('inst-state').textContent='Fast fertig…';
};
window.downloadDone=function(){
  setProgress(100);
  document.getElementById('upd-pop').classList.add('ready');
  document.getElementById('inst-state').textContent='Bereit! Programm startet neu…';
};
async function doInstall(){
  if(!(UPD && UPD.frozen)){   // Dev/keine .exe -> nur Release-Seite oeffnen
    try{ await api.install_update(); }catch(_){}
    closeOverlay('overlay-update'); return;
  }
  startInstallUI();
  let r=null; try{ r=await api.install_update(); }catch(_){}
  if(r && !r.ok){   // Fehler -> zurueck zur Info-Ansicht
    const pop=document.getElementById('upd-pop');
    pop.classList.remove('installing','ready');
    toast('Update fehlgeschlagen: '+((r&&r.error)||'unbekannt'));
  }
  // bei Erfolg schliesst Python das Fenster nach der Animation
}
function dismissUpdate(){ document.getElementById('updatebar').classList.remove('show'); }
async function manualCheck(btn){
  btn.disabled=true; const s=document.getElementById('upd-status');
  s.className='badge'; s.textContent='Prüfe…';
  let u=null; try{ u=await api.check_update(); }catch(_){}
  if(u && u.available){ showUpdateBar(u); s.className='badge no'; s.textContent='v'+u.latest+' verfügbar';
    openUpdateDialog(); }
  else { s.className='badge ok'; s.textContent='Aktuell ✓'; }
  btn.disabled=false;
}

/* ---- Onboarding (erster Start) ---- */
const OB_ACCENTS=['#ec7456','#6c6cff','#3ecf8e','#4aa3ff','#ffb454','#ff6b6b','#c08cff','#34d6c8','#ffe066','#ff8fcf'];
const OB_STEPS=6;
let obStep=0;
function obShow(){
  const returning = !!STATE.settings.onboarded;
  if(returning){
    document.getElementById('ob-title').textContent = 'Neu in dieser Version ✨';
    document.getElementById('ob-intro').innerHTML =
      'Kurzer Rundgang – deine Einstellungen bleiben unberührt.<br><br>' +
      '<b>Neu:</b> Ein animierter Clawd-Buddy für deinen Desktop, der zeigt was Claude gerade macht. ' +
      'Neuer Tab „Buddy" mit allen Einstellungen – Position, Größe, Rahmen, Sichtbarkeit nur wenn Claude Code läuft.';
  }
  const cur=STATE.settings.accent;
  document.getElementById('ob-swatches').innerHTML=OB_ACCENTS.map(c=>
    `<div class="ob-sw ${c===cur?'active':''}" style="background:${c}" onclick="obPickAccent('${c}',this)"></div>`).join('');
  document.getElementById('ob-home').classList.toggle('on', STATE.settings.hide_home!==false);
  const f=document.getElementById('ob-folder');
  f.innerHTML = STATE.found
    ? ('📁 Sessions-Ordner gefunden:<br>'+esc(STATE.projects_dir))
    : '⚠️ Kein Sessions-Ordner gefunden – du kannst ihn später in den Einstellungen festlegen.';
  obStep=0; obRender();
  document.getElementById('onboard').classList.add('show');
}
function obPickAccent(c,el){
  applyAccent(c); api.update_setting('accent',c);
  document.querySelectorAll('.ob-sw').forEach(s=>s.classList.remove('active'));
  el.classList.add('active');
}
function obToggleHome(el){
  const on=!el.classList.contains('on'); el.classList.toggle('on',on);
  api.update_setting('hide_home',on);
}
function obRender(){
  document.querySelectorAll('.ob-step').forEach(s=>{ s.hidden = (+s.dataset.step!==obStep); });
  let dots=''; for(let i=0;i<OB_STEPS;i++) dots+=`<i class="${i===obStep?'on':''}"></i>`;
  document.getElementById('ob-dots').innerHTML=dots;
  document.getElementById('ob-back').style.visibility = obStep===0?'hidden':'visible';
  document.getElementById('ob-next').textContent = obStep===OB_STEPS-1 ? "Los geht's! 🎉" : 'Weiter';
}
function obNext(){ if(obStep<OB_STEPS-1){ obStep++; obRender(); } else obFinish(); }
function obPrev(){ if(obStep>0){ obStep--; obRender(); } }
async function obFinish(){
  await api.update_setting('onboarded',true);
  ingest(await api.update_setting('onboarded_version', STATE.onboarding_version || ''));
  document.getElementById('onboard').classList.remove('show');
  render(); renderSettings();
}

/* ---- Tastatur ---- */
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){closeOverlay('overlay-color');closeOverlay('overlay-rename');}
  if(e.key==='F11'){ e.preventDefault(); try{api.toggle_fullscreen();}catch(_){}}
  if(e.key==='F2' && getSel()) openRename();
  if(e.key==='Enter'){
    if(document.getElementById('overlay-rename').classList.contains('show')) saveRename();
    else if(getSel() && document.activeElement.id!=='search') doResume();
  }
});
document.getElementById('search').addEventListener('input',render);
document.addEventListener('pointermove',e=>{ if(CPdrag) cpPick(e); });
document.addEventListener('pointerup',()=>{ CPdrag=false; });
document.addEventListener('pointercancel',()=>{ CPdrag=false; });
window.addEventListener('blur',()=>{ CPdrag=false; });

function whenReady(){
  if(window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_state === 'function'){
    api = window.pywebview.api; boot();
  } else {
    setTimeout(whenReady, 80);
  }
}
whenReady();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
#  Selbst-Installation (beim ersten Doppelklick der heruntergeladenen .exe)
# --------------------------------------------------------------------------- #
def _autostart_target_exe():
    """Pfad der zu startenden EXE fuer Autostart (installierte Kopie)."""
    if getattr(sys, "frozen", False):
        # Installierte Version bevorzugen, sonst laufende
        installed = os.path.join(install_dir(), "ClaudeSessionBrowser.exe")
        if os.path.isfile(installed):
            return installed
        return os.path.abspath(sys.executable)
    return None  # Dev-Modus: kein Autostart-Eintrag


def set_autostart(enable):
    """Windows-Autostart via HKCU\\...\\Run. `enable=False` entfernt Eintrag."""
    if not _IS_WIN:
        return False
    try:
        import winreg
    except Exception:
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = "ClaudeSessionBrowser"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_ALL_ACCESS) as k:
            if enable:
                exe = _autostart_target_exe()
                if not exe:
                    return False
                # In Anfuehrungszeichen setzen (Pfad mit Leerzeichen)
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def is_autostart_enabled():
    """Liest den aktuellen Autostart-Status aus der Registry und prueft
    dass die referenzierte .exe wirklich existiert (verwaiste Eintraege
    werden als 'nicht aktiv' behandelt)."""
    if not _IS_WIN:
        return False
    try:
        import winreg
    except Exception:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_READ) as k:
            try:
                v, _ = winreg.QueryValueEx(k, "ClaudeSessionBrowser")
                if not v:
                    return False
                # Pfad extrahieren – v ist typischerweise '"C:\...\exe"'
                path = str(v).strip().strip('"')
                if not os.path.isfile(path):
                    # Verwaister Eintrag – gleich entsorgen
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                                            0, winreg.KEY_ALL_ACCESS) as k2:
                            winreg.DeleteValue(k2, "ClaudeSessionBrowser")
                    except Exception:
                        pass
                    return False
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False


def install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
    return os.path.join(base, "ClaudeSessionBrowser")


def _ps_escape(s):
    """Escaped einen String fuer PowerShell-Single-Quote-Literals.
    In PS werden `'` innerhalb '...' durch '' escaped."""
    return str(s).replace("'", "''")


def _make_shortcuts(target):
    wd = os.path.dirname(target)
    targets = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        targets.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                                    "Programs", "Claude Session Browser.lnk"))
    targets.append(os.path.join(HOME, "Desktop", "Claude Session Browser.lnk"))
    # Pfade escapen – ein `'` in einem Username (z.B. "O'Brien") wuerde sonst
    # aus dem String ausbrechen und PowerShell-Code injizieren.
    t_e = _ps_escape(target)
    wd_e = _ps_escape(wd)
    for lnk in targets:
        lnk_e = _ps_escape(lnk)
        ps = ("$w=New-Object -ComObject WScript.Shell; "
              f"$s=$w.CreateShortcut('{lnk_e}'); $s.TargetPath='{t_e}'; "
              f"$s.WorkingDirectory='{wd_e}'; $s.IconLocation='{t_e},0'; "
              "$s.Save()")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        except OSError:
            pass


def self_install():
    """Wird die heruntergeladene .exe ausserhalb des Install-Ordners gestartet,
    kopiert sie sich nach %LOCALAPPDATA% (beschreibbar, ohne Web-Markierung),
    legt eine Verknuepfung an und startet von dort. Gibt True zurueck, wenn die
    aufrufende Instanz sich beenden soll."""
    if not getattr(sys, "frozen", False):
        return False
    cur = os.path.abspath(sys.executable)
    target = os.path.join(install_dir(), "ClaudeSessionBrowser.exe")
    if os.path.normcase(cur) == os.path.normcase(target):
        return False  # laeuft bereits aus dem Install-Ordner
    try:
        os.makedirs(install_dir(), exist_ok=True)
        try:
            # shutil.copy kopiert KEINE Alternate-Data-Streams -> Zone.Identifier
            # (die "aus dem Web"-Markierung) faellt automatisch weg
            shutil.copy(cur, target)
        except OSError:
            if not os.path.exists(target):
                return False  # konnte nicht installieren -> normal weiterlaufen
            # Ziel evtl. gesperrt (laeuft schon) -> einfach die vorhandene starten
        _make_shortcuts(target)
        subprocess.Popen([target], creationflags=0x00000008)  # DETACHED
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
def main():
    if self_install():
        return  # heruntergeladene Instanz beendet sich; installierte Kopie laeuft
    api = Api()
    s = api.settings
    kw = dict(
        html=build_html(), js_api=api, min_size=(820, 520),
        resizable=True, background_color="#14100e",
        width=int(s.get("win_w") or 1180),
        height=int(s.get("win_h") or 760),
        maximized=bool(s.get("win_max")),
    )
    if s.get("win_x") is not None and s.get("win_y") is not None:
        kw["x"] = int(s["win_x"])
        kw["y"] = int(s["win_y"])
    win = webview.create_window("Claude Session Browser", **kw)
    api.bind_window(win)

    # Autostart: beim ersten Start eintragen wenn Default aktiv ist.
    # Der Nutzer kann in den Einstellungen abschalten.
    if getattr(sys, "frozen", False):
        want_autostart = bool(s.get("autostart", True))
        already = is_autostart_enabled()
        if want_autostart and not already:
            if set_autostart(True):
                s["autostart_registered"] = True
                save_json(SETTINGS_FILE, s)
        elif not want_autostart and already:
            set_autostart(False)

    # Buddy automatisch anwerfen, wenn er zuletzt an war.
    if s.get("buddy", {}).get("enabled"):
        try:
            api.buddy.start()
        except Exception:
            pass

    # System-Tray – aktiv wenn "close_to_tray" gesetzt ist (Default).
    _quit_wanted = {"v": False}

    def real_quit():
        _quit_wanted["v"] = True
        try:
            for w in list(webview.windows):
                w.destroy()
        except Exception:
            pass

    tray = TrayManager(lambda: (webview.windows[0] if webview.windows else None),
                       real_quit)
    if s.get("close_to_tray", True):
        tray.start()

    def on_before_close():
        # Rueckgabewert True erlaubt Schliessen, False verhindert es.
        # WICHTIG: Nur in den Tray verstecken wenn das Tray-Icon auch
        # tatsaechlich laeuft – sonst haette der User keine Moeglichkeit
        # das versteckte Fenster wieder zu holen (Zombie-Prozess).
        if (api.settings.get("close_to_tray", True)
                and not _quit_wanted["v"]
                and tray.icon is not None):
            try:
                win.hide()
            except Exception:
                pass
            return False
        return True

    try:
        win.events.closing += on_before_close
    except Exception:
        pass

    # Fuers UI erreichbar machen: echtes Beenden ueber die App
    api._real_quit = real_quit
    api._tray = tray

    try:
        webview.start()
    finally:
        try:
            api.buddy.stop()
        except Exception:
            pass
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
