# -*- coding: utf-8 -*-
"""Drive health, read from Windows.

What this reads and what it needs:

    Get-PhysicalDisk                no rights needed  - name, type, size, health
    Get-Partition                   no rights needed  - which volume sits on which disk
    Get-Volume                      no rights needed  - how full a volume is
    Get-StorageReliabilityCounter   administrator     - wear, hours, temperature

The last one is refused without elevation, so it is asked for separately and
its absence is reported rather than hidden: a drive panel that silently leaves
out the wear figure is worse than one that says it cannot read it.

All four run in a single PowerShell call. Starting PowerShell costs about two
seconds, so one call per query made the tab take seven seconds to fill.
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
$out = @{ disks = @(); volumes = @(); partitions = @(); wear = @(); wear_error = '' }
try {
  $out.disks = @(Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,
                 BusType,HealthStatus,OperationalStatus,Size,SerialNumber,FirmwareVersion)
} catch { $out.disk_error = $_.Exception.Message }
try {
  $out.partitions = @(Get-Partition | Where-Object DriveLetter |
                      Select-Object DiskNumber,DriveLetter,Size)
} catch { }
try {
  $out.volumes = @(Get-Volume | Where-Object DriveLetter |
                   Select-Object DriveLetter,FileSystemLabel,FileSystem,HealthStatus,
                                 OperationalStatus,Size,SizeRemaining)
} catch { }
try {
  $out.wear = @(Get-PhysicalDisk | Get-StorageReliabilityCounter |
                Select-Object DeviceId,Wear,Temperature,TemperatureMax,PowerOnHours,
                              StartStopCycleCount,ReadErrorsTotal,ReadErrorsCorrected,
                              ReadErrorsUncorrected,WriteErrorsTotal,
                              WriteErrorsUncorrected,FlushLatencyMax,LoadUnloadCycleCount)
} catch { $out.wear_error = $_.Exception.Message }
$out | ConvertTo-Json -Depth 4 -Compress
"""


def setup(host):
    global HOST
    HOST = host


def _liste(x):
    return x if isinstance(x, list) else ([x] if x else [])


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

    wear = {str(w.get("DeviceId")): w for w in _liste(d.get("wear"))}
    # Laufwerksbuchstabe -> Plattennummer. Get-PhysicalDisk.DeviceId und
    # Get-Partition.DiskNumber sind dieselbe Nummer, darueber laeuft die
    # Zuordnung; ohne sie steht nicht da, welches Laufwerk auf welcher Platte
    # liegt - genau die Frage, die man bei mehreren Platten hat.
    platte_von = {}
    for part in _liste(d.get("partitions")):
        letter = str(part.get("DriveLetter") or "").strip()
        if letter:
            platte_von[letter] = str(part.get("DiskNumber"))

    volumes = []
    for v in _liste(d.get("volumes")):
        letter = str(v.get("DriveLetter") or "").strip()
        volumes.append({
            "letter": letter,
            "label": v.get("FileSystemLabel") or "",
            "filesystem": v.get("FileSystem") or "",
            "health": v.get("HealthStatus") or "",
            "operational": v.get("OperationalStatus") or "",
            "size": v.get("Size") or 0,
            "free": v.get("SizeRemaining") or 0,
            "disk": platte_von.get(letter, ""),
        })

    disks = []
    for disk in _liste(d.get("disks")):
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
            "serial": (disk.get("SerialNumber") or "").strip(),
            "firmware": (disk.get("FirmwareVersion") or "").strip(),
            "volumes": [v["letter"] for v in volumes if v["disk"] == kennung],
            "smart": {
                "wear": w.get("Wear"),
                "temperature": w.get("Temperature"),
                "temperature_max": w.get("TemperatureMax"),
                "hours": w.get("PowerOnHours"),
                "power_cycles": w.get("StartStopCycleCount"),
                "read_errors": w.get("ReadErrorsTotal"),
                "read_errors_corrected": w.get("ReadErrorsCorrected"),
                "read_errors_uncorrected": w.get("ReadErrorsUncorrected"),
                "write_errors": w.get("WriteErrorsTotal"),
                "write_errors_uncorrected": w.get("WriteErrorsUncorrected"),
            } if w else {},
        })

    return {
        "disks": disks,
        "volumes": volumes,
        "smart_available": bool(wear),
        "smart_error": str(d.get("wear_error") or "")[:200],
        "source": "Get-PhysicalDisk / Get-Volume",
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
