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


def _reset_minutes(reset_ts: str, now: float) -> int:
    """Reset-Header (Epoch-Sekunden) -> verbleibende Minuten."""
    try:
        ts = float(reset_ts)
    except (TypeError, ValueError):
        return 0
    mins = (ts - now) / 60.0
    return int(round(mins)) if mins > 0 else 0


def poll_usage(token: str) -> dict | None:
    """Fragt die Ratelimit-Header bei der Anthropic-API ab.

    Schickt eine Mini-Anfrage (1 Token) -- die Antwort interessiert nicht,
    nur die Header. Gibt den fertigen BLE-Payload zurueck oder None."""
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
            return None
    except Exception:
        return None

    def hdr(name: str, default: str = "") -> str:
        return hdrs.get(name) or default

    now = time.time()
    if hdr("anthropic-ratelimit-unified-5h-utilization"):
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
        return None

    # Uhrzeit fuers Display (lokale Wall-Clock als Epoch)
    payload["t"] = int(time.time()) + time.localtime().tm_gmtoff
    payload["tf"] = 24
    return payload


# --------------------------------------------------------------------------
# Geraeteadresse
# --------------------------------------------------------------------------

def _mac_from_instance_id(instance_id: str) -> str | None:
    m = re.search(r"DEV_([0-9A-Fa-f]{12})(?![0-9A-Fa-f])", instance_id)
    if not m:
        return None
    h = m.group(1).upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def discover_address() -> str | None:
    """Findet die BLE-Adresse des gekoppelten Clawdmeters ueber die PnP-Tabelle.

    Ein gekoppeltes UND verbundenes Geraet sendet keine Advertisements mehr,
    ein normaler BLE-Scan findet es also nicht. Windows kennt die Adresse aber."""
    if override := os.environ.get("CLAWDMETER_BLE_ADDRESS"):
        return override.strip().upper()
    if sys.platform != "win32":
        return None
    command = (
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.FriendlyName -eq '{DEVICE_NAME}' }} | "
        "Select-Object -ExpandProperty InstanceId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        mac = _mac_from_instance_id(line.strip())
        if mac:
            return mac
    return None


# --------------------------------------------------------------------------
# Hintergrund-Link
# --------------------------------------------------------------------------

class ClawdmeterLink:
    """Haelt die BLE-Verbindung zum Clawdmeter und schickt zyklisch Updates.

    Laeuft in einem eigenen Thread mit eigener asyncio-Loop, damit die
    pywebview-GUI davon nichts mitbekommt."""

    def __init__(self, log=None):
        self._log = log or (lambda msg: None)
        self._thread = None
        self._stop = threading.Event()
        self._status = {"connected": False, "last_send": None,
                        "last_error": None, "address": None}
        self._lock = threading.Lock()

    # -- oeffentliche API --------------------------------------------------

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
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
            address = discover_address()
            if not address:
                self._set(connected=False,
                          last_error="Geraet nicht gekoppelt")
                self._log("Clawdmeter nicht gefunden - erst in den "
                          "Windows-Bluetooth-Einstellungen koppeln")
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

        async with BleakClient(address) as client:
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
        payload = await asyncio.to_thread(poll_usage, token)
        if not payload:
            self._set(last_error="API-Abfrage fehlgeschlagen")
            return
        data = json.dumps(payload, separators=(",", ":")).encode()
        await client.write_gatt_char(RX_CHAR_UUID, data, response=False)
        self._set(last_send=time.time(), last_error=None)
        self._log(f"Clawdmeter: {payload['s']}% / {payload['w']}%")


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
        _p(f"Payload: {poll_usage(tok)}")

    link = ClawdmeterLink(log=_p)
    link.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        link.stop()
