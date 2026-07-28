"""
Clawdmeter-Anbindung fuer den Claude Session Browser.

Ersetzt den separaten Clawdmeter-Daemon: der Session Browser pollt selbst
die Anthropic-API nach den Ratelimit-Headern und schickt die Werte per BLE
an das Geraet.

Protokoll (aus der Clawdmeter-Firmware):
  Service  4c41555a-4465-7669-6365-000000000001
    ...0002  write     -> JSON-Payload mit den Usage-Werten
    ...0003  read/notify
    ...0004  notify    -> Geraet bittet um frische Daten

Das Geraet muss einmalig ueber die Windows-Bluetooth-Einstellungen gekoppelt
werden (es ist zugleich eine BLE-HID-Tastatur). Danach findet dieses Modul
die Adresse selbst ueber die PnP-Tabelle.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

DEVICE_NAME = "Clawdmeter"
SERVICE_UUID = "4c41555a-4465-7669-6365-000000000001"
RX_CHAR_UUID = "4c41555a-4465-7669-6365-000000000002"
REQ_CHAR_UUID = "4c41555a-4465-7669-6365-000000000004"

API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.5",
}
API_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}

POLL_INTERVAL = 60      # Sekunden zwischen zwei API-Abfragen
RETRY_INTERVAL = 15     # Wartezeit nach einem Verbindungsfehler
CONNECT_TIMEOUT = 20    # Abbruch wenn das Geraet nicht antwortet


# --------------------------------------------------------------------------
# Token
# --------------------------------------------------------------------------

def _credential_candidates() -> list[Path]:
    if override := os.environ.get("CLAUDE_CREDENTIALS_PATH"):
        return [Path(override)]
    if config_dir := os.environ.get("CLAUDE_CONFIG_DIR"):
        return [Path(config_dir) / ".credentials.json"]
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return [
        home / ".claude" / ".credentials.json",
        local / "Claude" / ".credentials.json",
        roaming / "Claude" / ".credentials.json",
    ]


def _extract_token(blob: str) -> str | None:
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        tok = data.get("accessToken")
        if isinstance(tok, str) and tok.strip():
            return tok
        for v in data.values():
            if isinstance(v, dict):
                tok = v.get("accessToken")
                if isinstance(tok, str) and tok.strip():
                    return tok
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    return None


def read_token() -> str | None:
    """Liest den Claude-OAuth-Token aus der ersten gefundenen Credentials-Datei."""
    for path in _credential_candidates():
        try:
            tok = _extract_token(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if tok:
            return tok
    return None


# --------------------------------------------------------------------------
# API-Abfrage
# --------------------------------------------------------------------------

def _pct(value: str) -> int:
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def _reset_epoch(reset_ts: str) -> float:
    """Reset-Header (Epoch-Sekunden) -> float. 0.0 wenn unlesbar."""
    try:
        return float(reset_ts)
    except (TypeError, ValueError):
        return 0.0


def _reset_minutes(reset_ts: str, now: float) -> int:
    """Reset-Header (Epoch-Sekunden) -> verbleibende Minuten."""
    ts = _reset_epoch(reset_ts)
    mins = (ts - now) / 60.0
    return int(round(mins)) if mins > 0 else 0


def poll_usage(token: str) -> dict | None:
    """Wie poll_usage_meta, aber nur der BLE-Payload."""
    payload, _meta = poll_usage_meta(token)
    return payload


def poll_usage_meta(token: str) -> tuple[dict | None, dict]:
    """Fragt die Ratelimit-Header bei der Anthropic-API ab.

    Schickt eine Mini-Anfrage (1 Token) -- die Antwort interessiert nicht,
    nur die Header. Gibt (BLE-Payload, Meta) zurueck; Payload ist None wenn
    die Abfrage fehlschlug.

    Meta traegt die rohen Reset-Zeitpunkte als Epoch. Der Payload kann die
    nicht liefern: der schickt dem Geraet nur noch verbleibende MINUTEN, und
    aus einer gerundeten Minutenzahl laesst sich kein stabiler Zeitpunkt
    zurueckrechnen -- fuer den Reset-Timer brauchen wir aber genau den."""
    headers = dict(API_HEADERS)
    headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(API_BODY).encode()
    req = urllib.request.Request(API_URL, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            hdrs = resp.headers
    except urllib.error.HTTPError as e:
        # 429 & Co. liefern die Header trotzdem mit
        hdrs = e.headers
        if e.code in (401, 403):
            return None, {}
    except Exception:
        return None, {}

    def hdr(name: str, default: str = "") -> str:
        return hdrs.get(name) or default

    now = time.time()
    meta = {}
    if hdr("anthropic-ratelimit-unified-5h-utilization"):
        meta = {
            "session_reset_at": _reset_epoch(
                hdr("anthropic-ratelimit-unified-5h-reset")),
            "weekly_reset_at": _reset_epoch(
                hdr("anthropic-ratelimit-unified-7d-reset")),
        }
        payload = {
            "s": _pct(hdr("anthropic-ratelimit-unified-5h-utilization")),
            "sr": _reset_minutes(hdr("anthropic-ratelimit-unified-5h-reset"), now),
            "w": _pct(hdr("anthropic-ratelimit-unified-7d-utilization")),
            "wr": _reset_minutes(hdr("anthropic-ratelimit-unified-7d-reset"), now),
            "st": hdr("anthropic-ratelimit-unified-5h-status", "unknown"),
            "acct": "pro",
            "ok": True,
        }
    elif hdr("anthropic-ratelimit-unified-overage-utilization"):
        meta = {
            "session_reset_at": _reset_epoch(
                hdr("anthropic-ratelimit-unified-overage-reset")),
            "weekly_reset_at": 0.0,
        }
        payload = {
            "s": _pct(hdr("anthropic-ratelimit-unified-overage-utilization")),
            "sr": _reset_minutes(hdr("anthropic-ratelimit-unified-overage-reset"), now),
            "w": 0,
            "wr": 0,
            "st": hdr("anthropic-ratelimit-unified-status", "unknown"),
            "acct": "ent",
            "ok": True,
        }
    else:
        return None, {}

    # Uhrzeit fuers Display (lokale Wall-Clock als Epoch)
    payload["t"] = int(time.time()) + time.localtime().tm_gmtoff
    payload["tf"] = 24
    meta["session_pct"] = payload["s"]
    meta["weekly_pct"] = payload["w"]
    meta["status"] = payload["st"]
    meta["at"] = now
    return payload, meta


# --------------------------------------------------------------------------
# Geraeteadresse
# --------------------------------------------------------------------------

def _mac_from_instance_id(instance_id: str) -> str | None:
    # Gross-/Kleinschreibung variiert: PnP liefert "DEV_", die Registry "Dev_".
    m = re.search(r"DEV_([0-9A-Fa-f]{12})(?![0-9A-Fa-f])", instance_id,
                  re.IGNORECASE)
    if not m:
        return None
    h = m.group(1).upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


# Kurz zwischenspeichern -- die Liste aendert sich praktisch nie.
_devices_cache: dict = {"at": 0.0, "value": []}
_DEVICES_TTL = 30.0

# Hier fuehrt Windows jedes gekoppelte BLE-Geraet mit Namen und Adresse.
_BTHLE_KEY = r"SYSTEM\CurrentControlSet\Enum\BTHLE"


def list_paired_devices(force: bool = False) -> list[dict]:
    """Alle in Windows gekoppelten BLE-Geraete: [{name, address}, ...].

    Liest die Registry direkt. Frueher lief das ueber PowerShell -- das
    riss aber jedes Mal ein Konsolenfenster auf, egal welche Flags gesetzt
    waren. Die Registry-Variante startet keinen Prozess und ist sofort da.

    Ein gekoppeltes UND verbundenes Geraet sendet keine Advertisements mehr,
    ein normaler BLE-Scan findet es also nicht -- Windows kennt es aber."""
    if sys.platform != "win32":
        return []
    now = time.time()
    if not force and now - _devices_cache["at"] < _DEVICES_TTL:
        return list(_devices_cache["value"])

    import winreg
    out, seen = [], set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BTHLE_KEY) as root:
            for i in range(4096):
                try:
                    dev = winreg.EnumKey(root, i)     # z.B. "Dev_98a316a5d60e"
                except OSError:
                    break
                mac = _mac_from_instance_id(dev)
                if not mac or mac in seen:
                    continue

                # Der Anzeigename haengt eine Ebene tiefer an der Instanz.
                name = ""
                try:
                    with winreg.OpenKey(root, dev) as devkey:
                        for j in range(64):
                            try:
                                inst = winreg.EnumKey(devkey, j)
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(devkey, inst) as ik:
                                    name = winreg.QueryValueEx(
                                        ik, "FriendlyName")[0]
                                if name:
                                    break
                            except OSError:
                                continue
                except OSError:
                    pass

                seen.add(mac)
                out.append({"name": (name or "").strip() or mac,
                            "address": mac})
    except OSError:
        return list(_devices_cache["value"])

    _devices_cache["at"] = now
    _devices_cache["value"] = out
    return list(out)


def _looks_like_clawdmeter(name: str) -> bool:
    return "clawd" in (name or "").lower()


def discover_address(preferred: str | None = None) -> str | None:
    """Ermittelt die zu benutzende BLE-Adresse.

    Reihenfolge:
      1. ausdruecklich gewaehltes Geraet (Einstellungen)
      2. CLAWDMETER_BLE_ADDRESS aus der Umgebung
      3. gekoppeltes Geraet dessen Name nach Clawdmeter aussieht
      4. wenn genau EIN BLE-Geraet gekoppelt ist: dieses
    """
    if preferred and preferred.strip():
        return preferred.strip().upper()
    if override := os.environ.get("CLAWDMETER_BLE_ADDRESS"):
        return override.strip().upper()

    devices = list_paired_devices()
    for d in devices:
        if _looks_like_clawdmeter(d["name"]):
            return d["address"]
    if len(devices) == 1:
        return devices[0]["address"]
    return None


# --------------------------------------------------------------------------
# Hintergrund-Link
# --------------------------------------------------------------------------

class ClawdmeterLink:
    """Haelt die BLE-Verbindung zum Clawdmeter und schickt zyklisch Updates.

    Laeuft in einem eigenen Thread mit eigener asyncio-Loop, damit die
    pywebview-GUI davon nichts mitbekommt."""

    def __init__(self, log=None, address_provider=None, on_usage=None):
        """address_provider: Funktion die die gewuenschte Geraeteadresse liefert
        (leer/None = automatisch suchen). Wird bei jedem Versuch neu gefragt,
        damit eine Aenderung in den Einstellungen sofort greift.

        on_usage: wird nach jeder erfolgreichen API-Abfrage mit dem Meta-Dict
        gerufen (siehe poll_usage_meta)."""
        self._log = log or (lambda msg: None)
        self._address_provider = address_provider or (lambda: None)
        self._on_usage = on_usage
        self._thread = None
        self._stop = threading.Event()
        self._status = {"connected": False, "last_send": None,
                        "last_error": None, "address": None}
        self._lock = threading.Lock()

    # -- oeffentliche API --------------------------------------------------

    def start(self) -> bool:
        old = self._thread
        if old and old.is_alive():
            if not self._stop.is_set():
                return True          # laeuft schon
            # Ein stop() ist noch im Gange: kurz auf das Ende warten, damit
            # nicht zwei Threads parallel auf dasselbe Geraet zugreifen.
            old.join(timeout=CONNECT_TIMEOUT + 5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="clawdmeter")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # -- intern ------------------------------------------------------------

    def _set(self, **kw) -> None:
        with self._lock:
            self._status.update(kw)

    async def _sleep(self, seconds: float) -> None:
        """Wartet, bricht aber sofort ab wenn stop() gerufen wurde."""
        import asyncio
        waited = 0.0
        while waited < seconds and not self._stop.is_set():
            await asyncio.sleep(0.5)
            waited += 0.5

    def _run(self) -> None:
        try:
            import asyncio
            asyncio.run(self._loop())
        except Exception as e:
            self._log(f"Clawdmeter-Thread beendet: {e}")
            self._set(connected=False, last_error=str(e))

    async def _loop(self) -> None:
        import asyncio
        while not self._stop.is_set():
            try:
                preferred = self._address_provider()
            except Exception:
                preferred = None
            address = discover_address(preferred)
            if not address:
                self._set(connected=False,
                          last_error="Kein Gerät ausgewählt")
                await self._sleep(RETRY_INTERVAL)
                continue
            self._set(address=address)
            try:
                await self._session(address)
                # Sauber beendete Sitzung (Geraet weg / abgeschaltet):
                # kurz warten, damit kein Reconnect-Karussell entsteht.
                await self._sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._set(connected=False, last_error=str(e))
                self._log(f"Clawdmeter-Verbindung verloren: {e}")
                await self._sleep(RETRY_INTERVAL)

    async def _session(self, address: str) -> None:
        import asyncio
        from bleak import BleakClient

        refresh = asyncio.Event()

        def on_refresh(_char, _data) -> None:
            refresh.set()

        async with BleakClient(address, timeout=CONNECT_TIMEOUT) as client:
            # Fehlt der Service, hat das zwei moegliche Gruende: falsches
            # Geraet gewaehlt -- oder das Geraet haengt schon an einem anderen
            # Programm, dann liefert Windows eine unvollstaendige Service-Liste.
            # Beides ist kein Grund aufzugeben, aber ein Schreibversuch waere
            # zwecklos. Also melden und es spaeter nochmal probieren.
            if not any(s.uuid.lower() == SERVICE_UUID
                       for s in client.services):
                self._set(connected=False, last_error=(
                    "Gerät meldet keinen Clawdmeter-Dienst — falsches Gerät, "
                    "oder es ist von einem anderen Programm belegt"))
                self._log(f"{address}: kein Clawdmeter-Dienst gefunden")
                await self._sleep(RETRY_INTERVAL)
                return

            self._set(connected=True, last_error=None)
            self._log(f"Clawdmeter verbunden ({address})")
            try:
                await client.start_notify(REQ_CHAR_UUID, on_refresh)
            except Exception:
                pass  # Refresh-Kanal ist optional, der Poll-Loop reicht

            while not self._stop.is_set() and client.is_connected:
                await self._send_once(client)
                # Auf den naechsten Poll warten -- oder frueher, wenn das
                # Geraet selbst um Daten bittet.
                # In kleinen Scheiben warten, damit stop() sofort greift.
                refresh.clear()
                waited = 0.0
                while (waited < POLL_INTERVAL and not self._stop.is_set()
                       and not refresh.is_set()):
                    try:
                        await asyncio.wait_for(refresh.wait(), 2.0)
                    except asyncio.TimeoutError:
                        waited += 2.0
        self._set(connected=False)

    async def _send_once(self, client) -> None:
        import asyncio
        token = read_token()
        if not token:
            self._set(last_error="Kein Claude-Token gefunden")
            return
        payload, meta = await asyncio.to_thread(poll_usage_meta, token)
        if not payload:
            self._set(last_error="API-Abfrage fehlgeschlagen")
            return
        # Die Limit-Ueberwachung mitfuettern, damit sie nicht dieselben
        # Header ein zweites Mal abfragen muss.
        if self._on_usage and meta:
            try:
                self._on_usage(meta)
            except Exception:
                pass
        data = json.dumps(payload, separators=(",", ":")).encode()
        await client.write_gatt_char(RX_CHAR_UUID, data, response=False)
        self._set(last_send=time.time(), last_error=None)
        self._log(f"Clawdmeter: {payload['s']}% / {payload['w']}%")


class UsageWatcher:
    """Haelt die Ratelimit-Werte aktuell, auch ohne Clawdmeter-Hardware.

    Der ClawdmeterLink fragt die Header nur ab solange eine BLE-Verbindung
    steht. Fuer die Limit-Benachrichtigungen brauchen wir die Werte aber
    unabhaengig davon, also pollt dieser Thread selbst -- allerdings nur
    wenn `is_covered()` False meldet, sonst wuerden zwei Poller dieselben
    Header holen und jeder Aufruf kostet echte Requests.

    Bewusst traege getaktet: den genauen Reset-Zeitpunkt liefert die API als
    Zeitstempel mit, den Rest erledigt ein Timer. Haeufiges Pollen wuerde
    daran nichts verbessern.
    """

    INTERVAL = 300.0        # 5 Minuten
    RETRY_INTERVAL = 60.0   # nach Fehlschlag frueher nochmal

    def __init__(self, on_usage, log=None, is_covered=None):
        self._on_usage = on_usage
        self._log = log or (lambda msg: None)
        self._is_covered = is_covered or (lambda: False)
        self._thread = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="usage-watcher")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            delay = self.INTERVAL
            if not self._is_covered():
                token = read_token()
                if token:
                    try:
                        _payload, meta = poll_usage_meta(token)
                    except Exception:
                        meta = {}
                    if meta:
                        try:
                            self._on_usage(meta)
                        except Exception as exc:
                            self._log(f"UsageWatcher: {exc}")
                    else:
                        delay = self.RETRY_INTERVAL
                else:
                    delay = self.RETRY_INTERVAL
            # In Scheiben warten, damit stop() sofort greift.
            waited = 0.0
            while waited < delay and not self._stop.is_set():
                self._stop.wait(1.0)
                waited += 1.0


# --------------------------------------------------------------------------
# Standalone-Test:  python clawdmeter.py
# --------------------------------------------------------------------------

if __name__ == "__main__":
    def _p(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    _p("Adresse suchen...")
    addr = discover_address()
    _p(f"Adresse: {addr}")
    _p("Token lesen...")
    tok = read_token()
    _p("Token: " + ("gefunden" if tok else "NICHT gefunden"))
    if tok:
        _p("API abfragen...")
        _pl, _mt = poll_usage_meta(tok)
        _p(f"Payload: {_pl}")
        _p(f"Meta:    {_mt}")

    link = ClawdmeterLink(log=_p)
    link.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        link.stop()
