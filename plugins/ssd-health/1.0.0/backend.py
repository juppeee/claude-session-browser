# -*- coding: utf-8 -*-
"""Drive health, read from Windows.

What this reads and what it needs:

    Get-PhysicalDisk                no rights needed  - name, type, size, health
    Get-Volume                      no rights needed  - how full a volume is
    Get-StorageReliabilityCounter   administrator     - wear, hours, temperature

The last one is refused without elevation, so it is asked for separately and
its absence is reported rather than hidden: a drive panel that silently leaves
out the wear figure is worse than one that says it cannot read it.

All three run in a single PowerShell call. Starting PowerShell costs about two
seconds, so three calls made the tab take seven seconds to fill.
"""

import json
import subprocess
import time

HOST = None
_CACHE = {"at": 0.0, "data": None}
_TTL = 60.0          # Platten aendern sich nicht im Sekundentakt
_TIMEOUT = 30

# Ausgabe ausdruecklich auf UTF-8: die Fehlermeldung bei fehlenden Rechten ist
# auf einem deutschen Windows deutsch, und mit der Standardkodierung bricht das
# Einlesen daran ab - ausgerechnet an der Meldung, die erklaert, was fehlt.
SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$out = @{ disks = @(); volumes = @(); wear = @(); wear_error = '' }
try {
  $out.disks = @(Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,
                 BusType,HealthStatus,OperationalStatus,Size)
} catch { $out.disk_error = $_.Exception.Message }
try {
  $out.volumes = @(Get-Volume | Where-Object DriveLetter |
                   Select-Object DriveLetter,FileSystemLabel,HealthStatus,Size,SizeRemaining)
} catch { }
try {
  $out.wear = @(Get-PhysicalDisk | Get-StorageReliabilityCounter |
                Select-Object DeviceId,Wear,Temperature,PowerOnHours,
                              ReadErrorsTotal,WriteErrorsTotal)
} catch { $out.wear_error = $_.Exception.Message }
$out | ConvertTo-Json -Depth 4 -Compress
"""


def setup(host):
    global HOST
    HOST = host


def _abfragen():
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    roh = (p.stdout or "").strip()
    if not roh:
        raise RuntimeError((p.stderr or "PowerShell lieferte nichts").strip()[:200])
    d = json.loads(roh)
    if d.get("disk_error"):
        raise RuntimeError(str(d["disk_error"])[:200])

    def liste(x):
        return x if isinstance(x, list) else ([x] if x else [])

    wear = {str(w.get("DeviceId")): w for w in liste(d.get("wear"))}
    disks = []
    for disk in liste(d.get("disks")):
        kennung = str(disk.get("DeviceId"))
        w = wear.get(kennung) or {}
        disks.append({
            "id": kennung,
            "name": disk.get("FriendlyName") or "?",
            "media": disk.get("MediaType") or "",
            "bus": disk.get("BusType") or "",
            "health": disk.get("HealthStatus") or "",
            "operational": disk.get("OperationalStatus") or "",
            "size": disk.get("Size") or 0,
            "wear": w.get("Wear"),
            "temperature": w.get("Temperature"),
            "hours": w.get("PowerOnHours"),
            "read_errors": w.get("ReadErrorsTotal"),
            "write_errors": w.get("WriteErrorsTotal"),
        })
    return {
        "disks": disks,
        "volumes": [{
            "letter": v.get("DriveLetter") or "",
            "label": v.get("FileSystemLabel") or "",
            "health": v.get("HealthStatus") or "",
            "size": v.get("Size") or 0,
            "free": v.get("SizeRemaining") or 0,
        } for v in liste(d.get("volumes"))],
        "wear_available": bool(wear),
        "wear_error": str(d.get("wear_error") or "")[:200],
        "at": time.time(),
    }


def call(method, args):
    if method == "drives":
        jetzt = time.time()
        if args.get("force") or not _CACHE["data"] or jetzt - _CACHE["at"] > _TTL:
            _CACHE["data"] = _abfragen()
            _CACHE["at"] = jetzt
        return _CACHE["data"]
    raise ValueError("unknown method: %s" % method)
