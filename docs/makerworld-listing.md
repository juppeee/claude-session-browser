# MakerWorld-Beitrag: Clawdmeter Case

Zum Kopieren in das Formular. Eckige Klammern = von dir zu ergaenzen.

---

## Modellname (max. 50 Zeichen)

```
Clawdmeter Case - desk buddy for Claude Code
```

44 Zeichen. „Desk buddy" deckt beides ab: den Verbrauch, den das Geraet ab
Werk zeigt, und das, was mit der App dazukommt - naemlich was Claude gerade
tut. „Usage" allein waere zu klein gedacht.

Alternativen:

```
Clawdmeter Case - see what Claude is doing      (42)
Clawdmeter Case - AI companion for your desk    (44)
Clawdmeter Case (Waveshare AMOLED 2.16)         (39, sachlich)
```

## Kategorie

**Gadgets** — dort liegen Gehaeuse fuer Elektronik. Zweite Wahl waere
*Hobby & DIY*.

## Tags

```
clawdmeter
claude
claude code
ai
esp32
esp32c6
waveshare
amoled
case
enclosure
desk toy
desk gadget
developer
programming
robot
pixel art
usage monitor
bluetooth
open source
functional print
```

---

## Beschreibung

```markdown
A little robot body for the **Clawdmeter** - the desk gadget that keeps an eye
on Claude for you.

On its own it shows how much of your quota is left. Paired with the Claude
Session Browser on Windows, it does rather more: it acts out what Claude is
doing right now - thinking, writing code, waiting for your permission, or out
of quota - as a pixel character on the screen.

Two parts, body and cap, printed without supports. The screen sits flush in
the front; arms and feet give the case the shape of the character that lives
on the display.

## What it holds

- **Waveshare ESP32-C6-Touch-AMOLED-2.16** [passt auch auf das S3-Modell? -> ergaenzen]
- A battery if you want it cordless [Typ/Groesse]

## Printing

| | |
|---|---|
| Layer height | [0.2 mm] |
| Walls | [3] |
| Infill | [15 %] |
| Supports | none |
| Material | [PLA] |
| Print time | [X h] |

Body goes on the plate with the open side down - no supports needed that way.

## Assembly

1. [Board von vorn einlegen, Display nach aussen]
2. [Akku in das Fach dahinter]
3. [Deckel aufsetzen bis er einrastet / mit 2x M2 verschrauben]

## The software behind it

The device and its firmware are the work of
[Hermann Björgvin](https://github.com/HermannBjorgvin/Clawdmeter) - flashing
instructions are in his repository. It shows your Claude usage and picks an
animation from how fast your quota is burning.

On Windows, the
[Claude Session Browser](https://github.com/juppeee/claude-session-browser)
connects over Bluetooth and mirrors its desktop buddy onto the device, so it
acts out what Claude is actually doing - thinking, writing code, waiting for
permission, out of quota - instead of only reacting to the burn rate.

Both are free and open source.
```

---

## Lizenz - zwei Dinge passen gerade nicht zusammen

Bei den Dateien steht **Open Source** angehakt, in den Lizenzfragen aber
dreimal **Nein**: keine Anpassungen, keine kommerzielle Nutzung, keine
Weitergabe. Das ist die restriktivste Einstellung, die es gibt.

Zum Projekt wuerde eher passen:

| Frage | Vorschlag |
|---|---|
| Weitergabe von Anpassungen? | **Ja** (oder „Ja, solange andere gleich teilen") |
| Kommerzielle Nutzung? | Deine Entscheidung - „Nein" ist ueblich |
| Weitergabe/Weiterverbreitung? | **Ja** |

Firmware und App sind beide quelloffen; ein Gehaeuse, das niemand anpassen
darf, wirkt daneben seltsam. Wer ein anderes Board hat, kann es sonst nicht
anpassen und laedt es auch nicht herunter.

---

## Bilder - hier ist Vorsicht geboten

MakerWorld schreibt ueber dem Cover-Feld ausdruecklich:
**„Bitte verwenden Sie echte Druckfotos."** Und unter Modellbilder:
**„Fotos des gedruckten Modells."**

Die drei erzeugten Bilder aus dem Downloads-Ordner sind **keine** Druckfotos.
Als Cover koennen sie den Beitrag kosten - MakerWorld entfernt Modelle, deren
Bilder das Gedruckte nicht zeigen.

Was du brauchst:

- **4:3** fuers Web-Cover und **3:4** fuers App-Cover (beide Pflicht bzw.
  empfohlen) - dasselbe Motiv, zweimal zugeschnitten
- ein paar Modellbilder: von vorn mit eingeschaltetem Display, von der Seite,
  die beiden Teile nebeneinander vor dem Zusammenbau

Tipps fuers Foto: Tageslicht am Fenster, schlichter Untergrund (Schreibtisch
reicht), Display an und den Buddy zeigend, Kamera auf Hoehe des Geraets.
Das Orange kommt vor dunklem Holz gut heraus.

Die Renderings kannst du als *zusaetzliches* Modellbild weiter hinten
einsortieren - nur nicht als Cover.

---

## Der Rest des Formulars

| Feld | Empfehlung |
|---|---|
| Sichtbarkeit | Oeffentlich |
| Community-Beitrag | aus |
| Exklusives Modellprogramm | nicht moeglich („Nicht berechtigt") |
| Stueckliste | aus - es gibt keine Kaufteile ausser Board und Akku |
| Dokumentation | optional; die Montageschritte stehen schon in der Beschreibung |

---

## Offen

1. Passt das Gehaeuse **nur auf das C6-Modell** oder auch auf das
   S3-Geschwister (ESP32-S3-Touch-AMOLED-2.16)? Beide haben laut Firmware
   dasselbe Panel. Wenn beide passen, gehoert das in Titel und Beschreibung.
2. Deckel geklemmt oder geschraubt? Wenn geschraubt: welche Schrauben?
3. Druckzeit und Einstellungen aus deinem Slicer.
4. Welcher Akku passt hinein?
