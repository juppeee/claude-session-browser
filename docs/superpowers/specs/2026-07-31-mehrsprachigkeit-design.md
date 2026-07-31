# Mehrsprachigkeit: Deutsch und Englisch

Aufgabe #42. Stand: 31.07.2026.

## Ziel

Die Oberflaeche gibt es bisher nur auf Deutsch. Das Projekt liegt oeffentlich
auf GitHub, wer es dort findet, kann es nicht bedienen. Kuenftig zeigt die App
Deutsch auf deutschen Windows-Systemen und Englisch auf allen anderen.

Der Installer ist davon ausgenommen: der ist **immer englisch**.

## Entscheidungen

**Startsprache richtet sich nach Windows.** Ist die Windows-Oberflaeche
deutsch, startet die App deutsch, sonst englisch. Umstellen laesst es sich
jederzeit. Gespeichert wird in den Einstellungen als `language` mit den
Werten `auto`, `de` oder `en`; Voreinstellung `auto`.

Ermittelt wird die Systemsprache ueber
`ctypes.windll.kernel32.GetUserDefaultUILanguage()`; die unteren 10 Bit
ergeben die Hauptsprache, `0x07` ist Deutsch.

**Der deutsche Satz ist der Schluessel.** Im Code steht
`t("Mit Windows starten")`, nicht `t('settings.autostart.label')`. Gruende:
es muessen keine 230 Schluessel erfunden und gepflegt werden, der Code bleibt
beim Lesen verstaendlich, und fehlt eine Uebersetzung, erscheint Deutsch statt
einer leeren Stelle.

Der bekannte Nachteil — zwei gleiche deutsche Saetze an Stellen, die
unterschiedliches Englisch braeuchten — ist hier vertretbar: die Texte sind
lang und konkret genug, dass Kollisionen unwahrscheinlich sind. Tritt doch
eine auf, bekommt eine der beiden Stellen einen minimal anderen deutschen
Wortlaut.

**Die Tabelle liegt in `i18n.py`.** `claude_sessions.py` hat bereits ueber
6600 Zeilen; weitere rund 500 gehoeren dort nicht hinein. Python und
JavaScript teilen sich dieselbe Tabelle: sie wird beim Start einmal in die
Oberflaeche gereicht.

**Umgeschaltet wird sofort**, ohne Neustart.

## Umfang

Rund 210 bis 235 Textstellen, verteilt auf drei Bereiche:

| Bereich | Anzahl | Weg |
|---|---|---|
| Python (Tray, Benachrichtigungen, Fehlermeldungen) | ~20-25 | `t(...)` |
| Festes HTML-Markup | ~55-60 | `data-t`-Markierung |
| JavaScript (Vorlagen fuer Einstellungen und Buddy) | ~130-150 | `t(...)` |

### Festes HTML

Reiter, Knoepfe, Platzhalter, die Einfuehrung und die Dialoge stehen als
festes Markup in der Vorlage. Sie bekommen `data-t` (Textinhalt),
`data-t-ph` (Platzhalter) und `data-t-title` (Kurzhinweis). Beim Start und
bei jedem Sprachwechsel laeuft ein Durchgang darueber.

Das Markup behaelt seinen deutschen Text — er ist ja der Schluessel. Faellt
die Uebersetzung aus, steht dort Deutsch, nichts bricht.

### Saetze mit Zahlen

Rund 15 Stellen bauen ihren Text zusammen. Der Platzhalter bleibt im
Schluessel stehen, damit die Wortstellung im Englischen frei ist:

```python
t("Clawdmeter hat nur noch {pct}% Akku").format(pct=pct)
```

Betroffen sind unter anderem die Akku-Warnung, die Limit-Vorwarnung
(`{pct}`, `{when}`, `{mins}`), `fmt_time` mit „heute/gestern/vor N Tagen",
`fmtDauer` (`d`, `h`, `min`, `s`), die Update-Meldungen mit Versionsnummer
und die Clawdmeter-Statuszeile mit den Sekunden seit der letzten Sendung.

**Eine Falle:** Fehlermeldungen aus dem Python-Teil werden im JavaScript in
einen weiteren Satz eingesetzt (`'Update fehlgeschlagen: ' + r.error`). Diese
Meldungen werden **nur einmal** uebersetzt, naemlich dort, wo sie entstehen.
Der umgebende Satz wird getrennt uebersetzt.

## Was nicht uebersetzt wird

- **Fenstertitel „Claude Session Browser"** — Produktname, und die App
  erkennt daran ihr eigenes Fenster. `_OWN_APP_TITLE_EXACT` (Zeile 1171) wird
  an vier Stellen exakt verglichen (1226, 1613, 2075, 6473). Aendert sich der
  Titel mit der Sprache, verschwindet der Buddy.
- **Animationsnamen** (`idle breathe`, `work coding` …) — sie sind an die
  Sprite-Daten gebunden und gehen so ans Clawdmeter.
- **Einstellungs-Schluessel** (`notify_limit_near`, `limit_warn_pct`,
  `when_claude` …) und die Farbschluessel (`warm`, `ocean` …). Bei den Farben
  ist nur der Anzeigename uebersetzbar, nicht der Schluessel.
- **Pfade und Programmnamen**: `HKCU\Run`, `wt`, `claude`, die GitHub-Adressen.
- **Clawd und Buddy** — Namen uebersetzt man nicht. Es bleibt bei „Dein
  Claude-Buddy" auf der Buddy-Seite und „Clawd-Buddy" im
  Clawdmeter-Abschnitt (siehe Gedaechtnisnotiz `csb-namensgebung`).

## Bedienung

In den Einstellungen unter **Darstellung** eine Auswahl **Sprache** mit
Automatisch / Deutsch / English. Nach dem Wechsel baut sich die Oberflaeche
neu auf.

Das Tray-Menue wird nicht neu erzeugt, sondern seine Beschriftungen werden
als Funktion hinterlegt (`pystray.MenuItem` nimmt fuer den Text auch etwas
Aufrufbares). Damit stimmt es nach dem Wechsel sofort.

## Installer

`setup.iss` fuehrt derzeit Deutsch und Englisch, Deutsch zuerst. Windows
zeigt deshalb vor der Installation noch eine Sprachauswahl.

Deutsch wird entfernt. Damit entfaellt der Dialog, und ein Klick weniger
steht zwischen Herunterladen und laufender App. Die beiden selbst
geschriebenen Texte (Zeile 75: „Beim Windows-Start automatisch mitstarten"
und die Gruppe „Systemintegration") werden ins Englische uebersetzt; alles
uebrige liefert Inno Setup mit.

Der Installer ist damit unabhaengig von der Windows-Sprache immer englisch,
die App richtet sich weiter nach dem System. Das ist so gewollt.

## Pruefung

Ein kleines Skript `tools/check_i18n.py` liest alle `t(...)`-Aufrufe und alle
`data-t`-Markierungen aus und gleicht sie mit der Tabelle ab. Es meldet:

1. Schluessel im Code, die in der englischen Tabelle fehlen
2. Eintraege in der Tabelle, die im Code nicht mehr vorkommen (Altlasten)
3. Schluessel mit Platzhaltern, deren Uebersetzung andere Platzhalter enthaelt
   — das faengt `{pct}` gegen `{percent}` ab, was sonst erst zur Laufzeit
   auffaellt

Das Skript laeuft vor jedem Bau.

### Von Hand zu pruefen

- Umschalten auf Englisch und zurueck, ohne Neustart, auf allen drei Reitern
- Tray-Menue nach dem Umschalten
- Buddy bleibt sichtbar (die Fenstererkennung darf nicht brechen)
- Clawdmeter bleibt verbunden und zeigt weiter die richtige Animation
- Einfuehrung beim ersten Start auf Englisch
- Installer laeuft ohne Sprachauswahl durch

## Uebersetzung

Die rund 230 Saetze uebersetzen Agenten in Bloecken. Vorgabe: natuerliches
Englisch, keine Wort-fuer-Wort-Uebertragung. Die deutschen Beschreibungen
sind stellenweise locker formuliert, das soll im Englischen ebenso klingen.

Fachbegriffe bleiben, wie Claude Code sie benutzt: Session bleibt Session,
nicht „Sitzung".

## Reihenfolge

1. `i18n.py` mit Geruest und Spracherkennung, `t()` in Python und JavaScript
2. Installer auf Englisch
3. Python-Texte umstellen (kleinste Gruppe, zeigt frueh ob der Weg traegt)
4. Festes Markup markieren
5. JavaScript umstellen — der grosse Block, in Abschnitten
6. Uebersetzen lassen
7. Pruefskript, dann Durchklicken

Nach Schritt 3 lohnt ein Zwischenstand: dann steht fest, ob `t()` in beiden
Welten sauber laeuft, bevor 190 weitere Stellen daran haengen.
