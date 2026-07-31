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
from html.parser import HTMLParser

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


class MarkupTexte(HTMLParser):
    """Sammelt aus dem festen Markup dasselbe ein wie collectStaticT() zur
    Laufzeit: sichtbare Texte sowie placeholder- und title-Attribute."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texte = set()
        self._stumm = 0   # in <script>/<style> stehen keine Nutzertexte

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._stumm += 1
        for name, wert in attrs:
            if name in ("placeholder", "title") and wert and wert.strip():
                self.texte.add(wert)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._stumm:
            self._stumm -= 1

    def handle_data(self, data):
        if self._stumm:
            return
        kern = data.strip()
        if kern:
            self.texte.add(kern)


def js_keys(quelltext):
    beginn = quelltext.find("HTML_TEMPLATE = r\"\"\"")
    if beginn < 0:
        return set(), "HTML_TEMPLATE nicht gefunden"
    block = quelltext[beginn:]
    out = set()
    for _, text in JS_AUFRUF.findall(block):
        out.add(text.replace("\\'", "'").replace('\\"', '"'))

    # Festes Markup: von </style> bis <script>. Was danach kommt, baut sich
    # zur Laufzeit auf und geht ohnehin durch t().
    von = block.find("</style>")
    bis = block.find("<script>")
    if von < 0 or bis < 0 or bis < von:
        return out, "Markup-Bereich nicht gefunden"
    p = MarkupTexte()
    p.feed(block[von:bis])
    out |= p.texte

    # Rohtext in den JavaScript-Vorlagen. Der steht nicht in t(), sondern
    # wird zur Laufzeit von translateDom() im fertigen Baum nachgeschlagen -
    # ohne ihn hier hielte die Pruefung jeden zweiten Eintrag fuer eine
    # Karteileiche.
    out |= js_rohtexte(block)
    out |= js_attribute(block)
    return out, None


# Einfache Zeichenketten im JavaScript. NICHT als „muss uebersetzt werden"
# zu verstehen - darunter ist jeder CSS-Klassenname. Nur dazu da, eine
# Karteileiche von einem Text zu unterscheiden, der ueber t(variable) laeuft:
# steht er irgendwo im Quelltext, ist er in Gebrauch.
JS_LITERAL = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")


def js_literale(block):
    beginn = block.find("<script>")
    ende = block.rfind("</script>")
    if beginn < 0 or ende < 0:
        return set()
    out = set()
    for _, text in JS_LITERAL.findall(block[beginn:ende]):
        if text:
            out.add(text.replace("\\'", "'").replace('\\"', '"'))
    return out


# title= und placeholder= innerhalb der Vorlagen. Die landen im fertigen
# Baum und werden dort uebersetzt - gebraucht wird also ein Eintrag dafuer.
JS_ATTR = re.compile(r'(?:title|placeholder)="([^"${}]+)"')


def js_attribute(block):
    beginn = block.find("<script>")
    ende = block.rfind("</script>")
    if beginn < 0 or ende < 0:
        return set()
    out = set()
    for lit in template_literale(block[beginn:ende]):
        for wert in JS_ATTR.findall(lit):
            kern = wert.strip()
            if kern and HAT_BUCHSTABE.search(kern):
                out.add(kern)
    return out


def template_literale(block):
    """Alle `…`-Zeichenketten. Ein einfacher Durchlauf statt eines Suchmusters:
    Backticks laufen ueber viele Zeilen und enthalten ${…}."""
    out = []
    i, n = 0, len(block)
    while i < n:
        if block[i] == "`":
            j = i + 1
            while j < n:
                if block[j] == "\\":
                    j += 2
                    continue
                if block[j] == "`":
                    break
                j += 1
            out.append(block[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


HAT_BUCHSTABE = re.compile(r"[A-Za-zÄÖÜäöüß]")
ZWISCHEN_TAGS = re.compile(r">([^<>{}`]+)<")
UNINTERESSANT = re.compile(
    r"^(?:[\d\s.,:;%/–—·✓✕▲▼…]+|[A-Za-z_]+\s*=|https?://\S+)$")


def js_rohtexte(block):
    beginn = block.find("<script>")
    ende = block.rfind("</script>")
    if beginn < 0 or ende < 0:
        return set()
    out = set()
    for lit in template_literale(block[beginn:ende]):
        ohne_werte = re.sub(r"\$\{[^{}]*\}", "\x00", lit)
        for treffer in ZWISCHEN_TAGS.findall(ohne_werte):
            kern = re.sub(r"\s+", " ", treffer).strip()
            if not kern or "\x00" in kern:
                continue
            if not HAT_BUCHSTABE.search(kern) or UNINTERESSANT.match(kern):
                continue
            out.add(kern)
    return out


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
    # Fuer die Karteileichen zaehlt jedes Vorkommen im Quelltext, auch als
    # blosser Listenwert - sonst gaelte alles als Altlast, was ueber
    # t(variable) laeuft.
    in_gebrauch = im_code | js_literale(quelltext)
    verwaist = sorted(k for k in tabelle if k not in in_gebrauch)

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
