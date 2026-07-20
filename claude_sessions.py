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
import shutil
import base64
import logging
import tempfile
import webbrowser
import datetime as dt
import subprocess
import urllib.request

import webview

# pywebview-Introspektions-Geschwaetz daempfen (harmlose COM-/Rekursionswarnungen)
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

# ----- Version & Update ---------------------------------------------------- #
VERSION = "1.0.9"
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
    "onboarded": False,          # Erst-Einrichtung schon durchlaufen?
}


def load_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return fallback


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    raw = load_json(SETTINGS_FILE, None)
    if raw:
        data.update(raw)
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


def resume_session(session_id, cwd, settings, project=""):
    # 1. das gespeicherte (Start-)Verzeichnis, wenn es existiert
    if cwd and os.path.isdir(cwd):
        workdir = cwd
    else:
        # 2. Notfall: aus dem Projektordner-Namen rekonstruieren (falls existent)
        dec = decode_project(project)
        workdir = dec if dec and os.path.isdir(dec) else HOME   # 3. HOME
    claude = settings.get("claude_cmd") or "claude"
    term = settings.get("terminal", "auto")
    try:
        if term in ("auto", "wt"):
            try:
                subprocess.Popen(["wt", "-d", workdir, "cmd", "/k",
                                  f"{claude} --resume {session_id}"])
                return {"ok": True}
            except FileNotFoundError:
                if term == "wt":
                    return {"ok": False, "error": "Windows Terminal (wt) nicht gefunden."}
        cmd = f'start "Claude Code" /D "{workdir}" cmd /k {claude} --resume {session_id}'
        subprocess.Popen(cmd, shell=True)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
#  API (von JavaScript aufrufbar)
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self.overrides = load_json(TITLES_FILE, {})
        self.settings = load_settings()
        self._cache = None

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

    def check_update(self):
        """Fragt bei GitHub nach einer neueren Version. Ohne Internet -> still."""
        frozen = bool(getattr(sys, "frozen", False))
        try:
            data = self._remote_info()
            self._update_info = data
            latest = data.get("version", "0")
            avail = _vtuple(latest) > _vtuple(VERSION)
            return {"available": avail, "latest": latest, "current": VERSION,
                    "url": data.get("url", ""), "notes": data.get("notes", ""),
                    "frozen": frozen}
        except Exception:
            return {"available": False, "current": VERSION, "frozen": frozen}

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
            with open(bat, "w", encoding="utf-8") as f:
                f.write(
                    "@echo off\r\n"
                    'set "CUR=' + cur + '"\r\n'
                    'set "NEW=' + new + '"\r\n'
                    "set /a n=0\r\n"
                    ":wait\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"
                    'move /y "%NEW%" "%CUR%" >nul 2>&1\r\n'
                    'if not exist "%NEW%" goto done\r\n'
                    "set /a n+=1\r\n"
                    "if %n% lss 60 goto wait\r\n"   # Abbruch nach ~2 Min
                    ":done\r\n"
                    "ping -n 2 127.0.0.1 >nul\r\n"  # kurz setzen lassen
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
      <h2>Willkommen 👋</h2>
      <p>Dein Browser für alle lokalen Claude-Code-Sessions – durchsuchen, einfärben und per Klick wieder einsteigen. Lass uns kurz einrichten – dauert nur eine Minute.</p>
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
      <button class="btn" onclick="resetTitle()">Auto-Titel</button>
      <button class="btn" onclick="closeOverlay('overlay-rename')">Abbrechen</button>
      <button class="btn accent" onclick="saveRename()">Speichern</button>
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
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

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
    if(!STATE.settings.onboarded) obShow();   // Erst-Einrichtung
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

function switchView(v){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===v));
  document.getElementById('view-sessions').classList.toggle('active',v==='sessions');
  document.getElementById('view-settings').classList.toggle('active',v==='settings');
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
function resetTitle(){const s=getSel(); if(s)document.getElementById('rename-input').value=s.auto_title;}
function closeOverlay(id){document.getElementById(id).classList.remove('show');}

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
  try{ const u=await api.check_update(); if(u&&u.available) showUpdateBar(u); }catch(_){}
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
const OB_STEPS=5;
let obStep=0;
function obShow(){
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
  ingest(await api.update_setting('onboarded',true));
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
def install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
    return os.path.join(base, "ClaudeSessionBrowser")


def _make_shortcuts(target):
    wd = os.path.dirname(target)
    targets = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        targets.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                                    "Programs", "Claude Session Browser.lnk"))
    targets.append(os.path.join(HOME, "Desktop", "Claude Session Browser.lnk"))
    for lnk in targets:
        ps = ("$w=New-Object -ComObject WScript.Shell; "
              "$s=$w.CreateShortcut('%s'); $s.TargetPath='%s'; "
              "$s.WorkingDirectory='%s'; $s.IconLocation='%s,0'; $s.Save()"
              % (lnk, target, wd, target))
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
    webview.start()


if __name__ == "__main__":
    main()
