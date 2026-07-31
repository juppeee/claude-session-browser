#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sammelt die deutschen Saetze, die uebersetzt werden muessen.

    python tools/collect_i18n.py > worklist.txt

Drei Quellen, dieselben, aus denen die App zur Laufzeit schoepft:

1. t("…") im Python-Teil
2. festes Markup (Texte, placeholder, title)
3. Rohtext in den JavaScript-Vorlagen - alles zwischen > und <, was nicht
   in ${…} steckt. Das ist genau das, was translateDom() spaeter im
   fertigen Baum vorfindet und nachschlaegt.

Ausgegeben wird nur, was in der englischen Tabelle noch fehlt.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import i18n            # noqa: E402
import check_i18n as pruef  # noqa: E402

# Ein Satz gilt als deutsch, wenn er einen Buchstaben enthaelt und nicht nur
# aus Zahlen, Zeichen oder einem einzelnen Wort in Grossbuchstaben besteht.
HAT_BUCHSTABE = re.compile(r"[A-Za-zÄÖÜäöüß]")

# Text zwischen den Tags, ohne eingesetzte Werte
ZWISCHEN_TAGS = re.compile(r">([^<>{}`]+)<")

# Offensichtlich technische Bruchstuecke, die nie auf dem Schirm landen
UNINTERESSANT = re.compile(
    r"^(?:[\d\s.,:;%/–—·✓✕▲▼…]+|[A-Za-z_]+\s*=|https?://\S+)$")


# Die Rohtext-Suche wohnt in check_i18n - beide Werkzeuge muessen dasselbe
# finden, sonst meldet das eine, was das andere fuer erledigt haelt.
js_rohtexte = pruef.js_rohtexte


def main():
    with open(pruef.QUELLE, encoding="utf-8") as fh:
        quelltext = fh.read()

    import ast
    baum = ast.parse(quelltext)
    aus_py = pruef.python_keys(baum)
    aus_markup, fehler = pruef.js_keys(quelltext)
    if fehler:
        print("FEHLER: %s" % fehler, file=sys.stderr)
        return 1
    aus_js = js_rohtexte(quelltext)

    tabelle = i18n.TRANSLATIONS.get("en") or {}
    alle = aus_py | aus_markup | aus_js
    offen = sorted(k for k in alle if k not in tabelle)

    print("# %d Saetze insgesamt, %d davon noch ohne englische Fassung"
          % (len(alle), len(offen)))
    print("# Python %d, Markup %d, JavaScript %d"
          % (len(aus_py), len(aus_markup), len(aus_js)))
    print()
    for k in offen:
        print(k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
