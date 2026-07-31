#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueft die Uebersetzungstabelle gegen den Code.

    python tools/check_i18n.py

Meldet drei Dinge:

1. Saetze, die im Code uebersetzt werden sollen, aber in der englischen
   Tabelle fehlen
2. Eintraege in der Tabelle, die im Code nicht mehr vorkommen (Altlasten)
3. Platzhalter, die links und rechts nicht zusammenpassen - {pct} gegen
   {percent} faellt sonst erst zur Laufzeit auf, und zwar dem Nutzer

Rueckgabewert 1, wenn etwas fehlt oder nicht zusammenpasst.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import i18n  # noqa: E402

QUELLE = os.path.join(ROOT, "claude_sessions.py")


def python_keys(baum):
    """t("…") im Python-Teil.

    Ueber den Syntaxbaum statt per Suchmuster - damit sind ueber mehrere
    Zeilen verteilte Texte automatisch zusammengesetzt, so wie Python es
    auch zur Laufzeit tut.
    """
    out = set()
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        f = knoten.func
        name = getattr(f, "id", None) or getattr(f, "attr", None)
        if name != "t" or not knoten.args:
            continue
        erst = knoten.args[0]
        if isinstance(erst, ast.Constant) and isinstance(erst.value, str):
            out.add(erst.value)
    return out


# t('…') und t("…") im JavaScript. Template-Literale mit ${…} sind bewusst
# nicht dabei: ein Schluessel mit eingesetztem Wert waere in der Tabelle nicht
# wiederzufinden. Dafuer gibt es die Platzhalter-Form t('… {x} …', {x: …}).
JS_AUFRUF = re.compile(r"\bt\(\s*(['\"])((?:\\.|(?!\1).)*?)\1")
JS_MARKE = re.compile(r"data-t(?:-ph|-title)?=\"([^\"]*)\"")


def js_keys(quelltext):
    beginn = quelltext.find("HTML_TEMPLATE = r\"\"\"")
    if beginn < 0:
        return set(), "HTML_TEMPLATE nicht gefunden"
    block = quelltext[beginn:]
    out = set()
    for _, text in JS_AUFRUF.findall(block):
        out.add(text.replace("\\'", "'").replace('\\"', '"'))
    for text in JS_MARKE.findall(block):
        if text:
            out.add(text)
    return out, None


def main():
    with open(QUELLE, encoding="utf-8") as fh:
        quelltext = fh.read()

    baum = ast.parse(quelltext)
    aus_py = python_keys(baum)
    aus_js, fehler = js_keys(quelltext)
    if fehler:
        print("FEHLER:", fehler)
        return 1

    im_code = aus_py | aus_js
    tabelle = i18n.TRANSLATIONS.get("en") or {}

    fehlend = sorted(k for k in im_code if k not in tabelle)
    verwaist = sorted(k for k in tabelle if k not in im_code)

    schief = []
    for schluessel, wert in sorted(tabelle.items()):
        links = i18n.placeholders(schluessel)
        rechts = i18n.placeholders(wert)
        if links != rechts:
            schief.append((schluessel, sorted(links), sorted(rechts)))

    print("Schluessel im Code: %d  (Python %d, JavaScript %d)"
          % (len(im_code), len(aus_py), len(aus_js)))
    print("Eintraege in der englischen Tabelle: %d" % len(tabelle))
    print()

    if fehlend:
        print("Ohne englische Uebersetzung (%d):" % len(fehlend))
        for k in fehlend:
            print("   %s" % k)
        print()

    if verwaist:
        print("In der Tabelle, aber nicht mehr im Code (%d):" % len(verwaist))
        for k in verwaist:
            print("   %s" % k)
        print()

    if schief:
        print("Platzhalter passen nicht zusammen (%d):" % len(schief))
        for k, links, rechts in schief:
            print("   %s" % k)
            print("      deutsch:  %s" % (links or "-"))
            print("      englisch: %s" % (rechts or "-"))
        print()

    if not (fehlend or verwaist or schief):
        print("Alles vollstaendig.")
        return 0
    # Verwaiste Eintraege allein sind kein Grund zu scheitern - sie stoeren
    # niemanden, sie sind nur unnoetiger Ballast.
    return 1 if (fehlend or schief) else 0


if __name__ == "__main__":
    sys.exit(main())
