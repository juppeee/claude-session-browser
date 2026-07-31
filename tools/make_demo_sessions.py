#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Erzeugt einen Beispiel-Sessionordner fuer Screenshots.

    python tools/make_demo_sessions.py [zielordner]

Ohne Angabe landet er unter %LOCALAPPDATA%\\csb-demo-sessions.

Danach in der App unter Einstellungen -> Sessions-Ordner diesen Pfad
auswaehlen, Bild machen, und hinterher wieder auf "Auto" stellen. Die
Beispiele sind erfunden - keine echten Pfade, keine echten Gespraeche.

Aufbau wie bei Claude Code: ein Unterordner je Projekt, darin eine .jsonl
je Session, eine Zeile je Nachricht.
"""

import json
import os
import sys
import time

STANDARD = os.path.join(os.environ.get("LOCALAPPDATA")
                        or os.path.expanduser("~"), "csb-demo-sessions")

STUNDE = 3600
TAG = 24 * STUNDE

# (Projektpfad, Kennung, Titel, erste Frage, Nachrichten du/Claude, Alter)
BEISPIELE = [
    (r"C:\Users\dev\projects\shop-frontend",
     "3f9a1c42-7b18-4d55-9e02-6a4c81d7e330",
     "Dark mode for the settings page",
     "Can you add a dark mode toggle to the settings page and remember the "
     "choice between restarts?",
     14, 31, 2 * STUNDE),

    (r"C:\Users\dev\projects\shop-frontend",
     "b7c2e850-1d94-4a6f-8c37-2f5b09ae4d16",
     "Checkout button stays disabled",
     "The checkout button stays disabled even when the cart has items in it. "
     "Any idea why?",
     9, 22, 5 * STUNDE),

    (r"C:\Users\dev\projects\billing-api",
     "e14d7a36-9f02-4b81-a5c6-8d3e7b110c45",
     "Refactor the payment webhook handler",
     "The webhook handler has grown to 600 lines. Let's split it up without "
     "changing behaviour.",
     23, 58, 26 * STUNDE),

    (r"C:\Users\dev\projects\billing-api",
     "a5b81f07-4c23-4e19-b7d8-05a9c6e2f831",
     "Why is the build 40 seconds slower?",
     "Our CI build went from 2:10 to 2:50 last week. Can you find out what "
     "changed?",
     11, 27, 30 * STUNDE),

    (r"C:\Users\dev\projects\billing-api",
     "c93e26b4-8a71-4f0d-9c15-3e7d4b8a2059",
     "Postgres backups to S3",
     "Set up a nightly Postgres dump that goes to S3 and keeps 30 days.",
     7, 19, 3 * TAG),

    (r"C:\Users\dev\tools\log-parser",
     "d20a4e91-6b35-4c88-a0f2-7c1e93b6d548",
     "Parse nginx logs into daily summaries",
     "I have 40 GB of nginx access logs. I'd like a daily summary per status "
     "code and endpoint.",
     18, 44, 4 * TAG),

    (r"C:\Users\dev\tools\log-parser",
     "f471c3a0-8e29-4a03-b6d1-9f2c85e70a34",
     "Flaky test in the retry suite",
     "test_retries_on_timeout fails maybe one run in ten. Can you make it "
     "deterministic?",
     6, 15, 5 * TAG),

    (r"C:\Users\dev\projects\portfolio",
     "8e3b0c57-2d41-4f96-8a7b-1c5e69d0b423",
     "Make the landing page load under a second",
     "Lighthouse says 3.4 s to first contentful paint. Where is that time "
     "going?",
     12, 29, 6 * TAG),

    (r"C:\Users\dev\projects\portfolio",
     "12f7d4a8-5b60-4e37-9c82-0a3f7b1d6e95",
     "Add an RSS feed to the blog",
     "Can you generate an RSS feed from the markdown posts at build time?",
     5, 11, 9 * TAG),

    (r"C:\Users\dev\scripts\home-lab",
     "6a0f8b23-7c94-4d51-b3e8-2f9a4c605d17",
     "Back up the Raspberry Pi to the NAS",
     "I want a weekly image of the Pi's SD card on the NAS, and older ones "
     "cleaned up.",
     8, 20, 12 * TAG),

    (r"C:\Users\dev\scripts\home-lab",
     "9d5c1e70-3a86-4b29-8f04-6e7b2d9a3c58",
     "Grafana dashboard for the heat pump",
     "The heat pump publishes over MQTT. I'd like flow temperature and power "
     "draw on one dashboard.",
     15, 37, 18 * TAG),

    (r"C:\Users\dev\projects\shop-frontend",
     "4b8e2f19-0d73-4a65-9b1c-8e5a3f7c024d",
     "Migrate the test suite to Vitest",
     "We're on Jest and it's slow. What would moving to Vitest involve?",
     21, 49, 25 * TAG),
]


def ordnername(pfad):
    """C:\\Users\\dev\\x -> C--Users-dev-x, so wie Claude Code es ablegt."""
    return pfad.replace(":", "-").replace("\\", "-").replace("/", "-")


def zeitstempel(epoche):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoche))


def schreibe(ziel, pfad, kennung, titel, frage, du, claude, alter):
    ordner = os.path.join(ziel, ordnername(pfad))
    os.makedirs(ordner, exist_ok=True)
    datei = os.path.join(ordner, kennung + ".jsonl")

    beginn = time.time() - alter - (du + claude) * 90
    zeilen = []
    zeilen.append({"type": "ai-title", "aiTitle": titel,
                   "cwd": pfad, "timestamp": zeitstempel(beginn)})
    zeilen.append({"type": "user", "cwd": pfad,
                   "timestamp": zeitstempel(beginn),
                   "message": {"role": "user", "content": frage}})

    # Der Rest traegt keinen Text - angezeigt werden nur die Anzahlen, und
    # erfundene Gespraechsverlaeufe waeren nur Ballast.
    t = beginn
    offen_du, offen_claude = du - 1, claude
    while offen_du or offen_claude:
        if offen_claude:
            t += 45
            zeilen.append({"type": "assistant", "cwd": pfad,
                           "timestamp": zeitstempel(t),
                           "message": {"role": "assistant",
                                       "content": [{"type": "text",
                                                    "text": "…"}],
                                       "stop_reason": "end_turn"}})
            offen_claude -= 1
        if offen_du:
            t += 120
            zeilen.append({"type": "user", "cwd": pfad,
                           "timestamp": zeitstempel(t),
                           "message": {"role": "user", "content": "…"}})
            offen_du -= 1

    with open(datei, "w", encoding="utf-8") as fh:
        for z in zeilen:
            fh.write(json.dumps(z, ensure_ascii=False) + "\n")

    # Die Spalte „Zuletzt aktiv" liest die Dateizeit, nicht die Zeitstempel.
    wann = time.time() - alter
    os.utime(datei, (wann, wann))
    return datei


def main():
    ziel = sys.argv[1] if len(sys.argv) > 1 else STANDARD
    os.makedirs(ziel, exist_ok=True)
    for eintrag in BEISPIELE:
        schreibe(ziel, *eintrag)
    print("%d Beispiel-Sessions in %d Projekten angelegt."
          % (len(BEISPIELE), len({e[0] for e in BEISPIELE})))
    print(ziel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
