# MakerWorld-Beitrag: Clawdmeter Case

Entwurf zum Kopieren. Was in eckigen Klammern steht, musst du noch
bestaetigen oder ersetzen.

---

## Titel

```
Clawdmeter Case — desk buddy for your Claude usage
```

Alternativen, falls dir das zu lang ist:

```
Clawdmeter Case (Waveshare AMOLED 2.16)
Clawdmeter — Claude usage monitor case
```

---

## Kurzbeschreibung

```
A little robot body for the Clawdmeter, the desk gadget that shows how much
of your Claude quota is left. Two parts, no supports, prints in about
[X] hours.
```

---

## Beschreibung

```markdown
The **Clawdmeter** is a small desk gadget that shows how much of your Claude
usage you have left — and, if you pair it with the Claude Session Browser on
Windows, what Claude is doing right now: thinking, writing code, waiting for
your permission, or out of quota.

This is a case for it. Two parts, a body and a cap, printed without supports.
The screen sits flush in the front, the arms and feet give it the look of the
little pixel character that lives on the display.

## What you need

- **Waveshare ESP32-C6-Touch-AMOLED-2.16** — the board the case is built around
- A battery, if you want it cordless [Typ/Groesse ergaenzen]
- The Clawdmeter firmware by [Hermann Björgvin](https://github.com/HermannBjorgvin/Clawdmeter)

## Printing

| | |
|---|---|
| Layer height | [0.2 mm] |
| Walls | [3] |
| Infill | [15 %] |
| Supports | none |
| Material | [PLA] |

Print the body with the open side down — no supports needed that way. The cap
[clips in / is held by two M2 screws].

## Assembly

1. [Board von vorn in den Rahmen legen, Display zeigt nach aussen]
2. [Akku in das Fach hinter dem Board]
3. [Deckel aufsetzen bis er einrastet]

## Software

The device and its firmware come from
[Hermann Björgvin](https://github.com/HermannBjorgvin/Clawdmeter) — flashing
instructions are in his repository.

If you're on Windows, the
[Claude Session Browser](https://github.com/juppeee/claude-session-browser)
connects to it over Bluetooth and mirrors its desktop buddy onto the device,
so it acts out what Claude is actually doing instead of just showing a usage
bar.

Both are free and open source.
```

---

## Deutsche Fassung

Falls du sie zusaetzlich willst — MakerWorld erlaubt nur einen Text, deshalb
entweder oder. Englisch erreicht mehr Leute.

```markdown
Der **Clawdmeter** ist ein kleines Geraet für den Schreibtisch, das zeigt,
wie viel von deinem Claude-Kontingent noch übrig ist. Zusammen mit dem
Claude Session Browser unter Windows zeigt er auch, was Claude gerade macht:
nachdenken, Code schreiben, auf deine Erlaubnis warten oder Limit erreicht.

Das hier ist das Gehäuse dazu. Zwei Teile, Korpus und Deckel, ohne Stützen
zu drucken. Das Display sitzt bündig vorn, Arme und Füße geben ihm die Form
der kleinen Pixelfigur, die auf dem Bildschirm wohnt.
```

---

## Tags

```
claude, ai, esp32, waveshare, amoled, desk, gadget, case, enclosure,
productivity, developer, monitor, robot
```

---

## Was ich noch von dir brauche

1. **Passt das Gehäuse nur auf das C6-Modell** oder auch auf das
   S3-Geschwister (ESP32-S3-Touch-AMOLED-2.16)? Beide haben laut Firmware
   dasselbe Panel. Wenn beide passen, gehoert das in die Beschreibung — das
   verdoppelt die Zielgruppe.
2. **Deckel: geklemmt oder geschraubt?** Und wenn geschraubt, welche Schrauben?
3. **Druckzeit und Einstellungen** aus deinem Slicer.
4. **Akku**: welcher passt rein?
