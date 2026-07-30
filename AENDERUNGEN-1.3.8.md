# Claude Session Browser 1.3.8 — was sich geändert hat

Stand: 30.07.2026. Sieben Commits seit 1.3.7 (`b3ecb4c` … `169a5b4`).
Noch **nicht** gebaut und **nicht** veröffentlicht — dies ist der Stand im
Quellcode zum Durchsehen.

Namen: die Überschrift der Buddy-Seite heisst jetzt **„Dein Claude-Buddy"**
(vorher „Dein Clawd-Buddy"), und im **Clawdmeter**-Abschnitt der Einstellungen
steht **„Clawd-Buddy spiegeln"**. Sonst bleibt der Wortlaut überall wie
gehabt.

---

## Behobene Fehler

### Das Limit wurde überhaupt nicht erkannt

Die echte Meldung von Claude Code sieht im Protokoll so aus:

```
type: "assistant", isApiErrorMessage: true, apiErrorStatus: 429, error: "rate_limit"
Text: "You've hit your session limit · resets 5:20pm (Europe/Berlin)"
```

Geprüft wurde bisher auf `isApiError` — **ohne** „Message". Wegen dieses einen
Wortes galt die Zeile nie als Fehler, und die gesamte Mustererkennung dahinter
lief nie an. Clawd zeigte beim vollen Limit irgendetwas, nur nicht `limit`.

Gegen die echte Zeile aus deiner Session getestet:

| | is_limit | Reset erkannt |
|---|---|---|
| vorher | `False` | – |
| nachher | `True` | 17:20 |

Zusätzlich werden jetzt Statuscode (429/401/5xx) und Fehlername direkt
ausgewertet, damit es eine geänderte Formulierung übersteht. Und `error` darf
auch ein blosser Text sein, nicht nur ein Objekt — im echten Datensatz ist es
der Text `"rate_limit"`.

### Laufendes Werkzeug wurde als Rückfrage gedeutet

„Noch kein Werkzeug-Ergebnis da" galt pauschal als „wartet auf deine
Erlaubnis". Das stimmt nur zur Hälfte — genauso gut läuft das Werkzeug einfach
noch. Während eines zweiminütigen Builds stand deshalb durchgehend die
`allow`-Animation auf dem Gerät, obwohl niemand gefragt wurde.

Ein sicheres Merkmal gibt es nicht: der Dialog „Do you want to proceed?" steht
nur im Terminal, im Protokoll landet davon nichts (nachgesehen: 779 Zeilen mit
Erlaubnis-Bezug, keine markiert eine offene Frage). Also über die Dauer — ein
laufendes Werkzeug liefert irgendwann ein Ergebnis, eine Rückfrage bleibt
stehen, bis jemand antwortet.

```
tool_use seit    1s -> tool_running           allow=False
tool_use seit   29s -> tool_running           allow=False
tool_use seit   31s -> tool_pending_approval  allow=True
```

**Bekannte Grenze:** ein Werkzeug, das länger als 30 Sekunden läuft, wird
weiterhin fälschlich als Rückfrage gedeutet. Sauber lösbar wäre das nur über
Claude-Code-Hooks — die feuern genau dann, wenn eine Erlaubnis angefragt wird.
Steht als eigener Ausbauschritt in den Notizen.

### Clawd blieb fünf Minuten auf „arbeitet" kleben

Einmal in einem Arbeitszustand, blieb es **fünf Minuten** bei `active` — egal
ob Claude längst nachdachte, Text schrieb oder wartete. Das sollte Flackern
verhindern, war dafür aber das falsche Mittel: gegen Flackern gibt es bereits
die Standzeit von 1,5 Sekunden beim Animationswechsel, mit sofortigem
Durchgriff für `limit`, `allow`, `surprise` und `wink`.

Das war neben der Rückfrage-Fehldeutung der zweite Grund, warum die
Animationen so oft nicht passten.

### Clawd erschien wegen eines Browser-Tabs

Die Browser-Ausschlussliste suchte im Fenstertitel nach `" - firefox"` —
Firefox schreibt aber `"— Mozilla Firefox"`. Ein Tab wie
„3D design claudeV6 – Tinkercad" enthält „claude", rutschte durch, und Clawd
erschien ohne jedes Terminal.

Titel zu raten ist ohnehin brüchig. Jetzt wird gefragt, **welches Programm**
das Fenster besitzt — das lässt sich durch keinen Seitentitel vortäuschen. An
deinem Rechner geprüft:

```
windowsterminal.exe  -> gezählt
firefox.exe          -> ignoriert
```

### Clawd platzieren ging nicht mehr

Beide Fenster sind „immer im Vordergrund", vorn liegt das zuletzt angehobene —
und `focus_force()` holte das Raster nach vorn, **nachdem** Clawd schon
angehoben war. Das Raster lag also darüber und fing jeden Mausklick ab:
Gitternetz erschien, Ziehen tat nichts, nur ESC kam durch.

Reihenfolge umgedreht, und das Raster ist zusätzlich für die Maus durchlässig
(`WS_EX_TRANSPARENT`) — damit sieht es die Maus gar nicht erst, auch wenn die
Z-Reihenfolge mal anders fällt. Tastatur bleibt davon unberührt.

### Das Fenster konnte unsichtbar starten

Windows meldet für ein **minimiertes** Fenster die Position −32000/−32000. Die
App merkte sich jede Bewegung, auch diese. Wer sie minimiert beendete, hatte
den Wert dauerhaft in den Einstellungen — und beim nächsten Start setzte sich
das Fenster dorthin: Eintrag in der Taskleiste, nichts auf dem Bildschirm,
auch nach Neustart. Dasselbe mit einer Position auf einem abgesteckten
Monitor.

Repariert an drei Stellen: der Wert wird gar nicht erst gespeichert, beim
Start wird die Position gegen die tatsächliche Bildschirmfläche geprüft, und
„Öffnen" im Tray holt ein verirrtes Fenster in die Mitte zurück.

*(Dieser Punkt war bereits in 1.3.7 — hier nur der Vollständigkeit halber.)*

---

## Neu

### Clawdmeter zeigt, was Clawd zeigt

Das Gerät spielt dieselbe Animation wie Clawd auf dem Desktop, statt selbst
eine nach Auslastung zu wählen. Abschaltbar über „Clawd spiegeln".

Der BLE-Payload hat dafür ein Feld `a` mit dem Animationsnamen; leer heisst,
das Gerät entscheidet wieder selbst. Animationswechsel gehen aus dem
zwischengespeicherten Payload raus, **ohne neue API-Abfrage** — sonst würde
jedes Blinzeln einen echten Request kosten.

Am seriellen Port mitgelesen und bestätigt:

```
17:41:04  splash: host -> work coding
17:41:07  splash: host -> allow
```

### Akkustand des Clawdmeter

Die Firmware meldet ihn über den Standard-Bluetooth-Batteriedienst — es
brauchte nichts Eigenes im Protokoll. Steht jetzt in der Statuszeile
(„Verbunden — zuletzt gesendet vor 12s · Akku 84 %") und warnt einmal beim
Unterschreiten einer einstellbaren Schwelle.

Die Sperre löst erst wieder aus, wenn der Stand die Schwelle um 10 Punkte
überschreitet — ohne diesen Abstand würde ein pendelnder Wert dauernd melden.
Über einen Verlauf mit Laden und erneutem Entladen getestet: genau zwei
Meldungen.

**Noch nicht am echten Gerät geprüft** — ob dein Clawdmeter den Ladestand
wirklich herausrückt, zeigt sich erst im Betrieb.

### Limit-Anzeige mit Countdown

Über den Benachrichtigungs-Schaltern steht der aktuelle Stand mit
Reset-Uhrzeit und sekündlich laufendem Countdown. Grün, gelb ab 90 %, rot
wenn voll. Funktioniert auch bei ausgeschaltetem Claude-Buddy.

### Schneller verbinden, Knopf zum sofortigen Verbinden

Der erste Anlauf lief 20 Sekunden in den Timeout, danach kamen pauschal 15
Sekunden Pause — bis zum zweiten Versuch vergingen 35 Sekunden. Jetzt kurz
anklopfen (8 s) und mit steigender Wartezeit nachfassen (2/4/8/15 s): **10
statt 35 Sekunden** bis zum zweiten Anlauf.

Dazu der Knopf „Jetzt verbinden". Er kürzt die laufende Wartezeit ab, statt
den Verbindungs-Thread neu zu starten — eine gerade aufgebaute Verbindung soll
dabei nicht wegfliegen. Getestet: 1 Sekunde statt 15.

Die Statuszeile zählt den Versuch mit („Verbinde… (3. Versuch)"), damit es
nicht wie ein Hänger aussieht.

---

## Oberfläche

- **Claude-Buddy-Tab:** alles unter dem Hauptschalter wird ausgegraut und
  unbedienbar, wenn der Buddy aus ist, mit einem Hinweis darüber.
- **Clawd in der Überschrift** war ein kleiner Klecks in einem schwarzen
  Quadrat. Die Sprites sind 20×20 Felder, die Figur belegt davon nur etwa
  15×13 — der Rest ist schwarze Fläche. Jetzt freigestellt, quadratisch
  aufgefüllt statt gedehnt, durchsichtiger Rand. Und in genau der Grösse
  geliefert, in der es hängt (40 px), mit ganzzahliger Vergrösserung —
  vorher wurde ein 108-px-Bild auf 34 gequetscht, Faktor 0,31, was
  Pixelgrafik zu Brei macht.
- **Detail-Panel:** Titel/Farbe/ID als Reihe statt drei gestapelter Knöpfe.
  Beschriftung bleibt drunter — „Farbe" und „ID kopieren" errät man an einem
  Symbol allein nicht.
- **Fusszeile** mit Tastaturkürzeln auf allen Tabs, Inhalt je Ansicht. Nur
  was der Tastatur-Handler wirklich kann: `Strg+F` und `F5` gibt es in der App
  gar nicht, die wären beinahe drin gelandet.
- **Sprungleiste** über den Einstellungen. Baut sich aus den vorhandenen
  Sektionsbändern auf, die aktive Gruppe wandert beim Scrollen mit.
- **Farbkacheln** haben sichtbares Hover-Feedback. Vorher gab es zwar einen
  Effekt, aber nur 10 % in 0,08 Sekunden — das sieht man schlicht nicht.
- **Statuspunkt** vor der Clawdmeter-Verbindung: grün, gelb pulsierend, rot,
  grau.
- **Warnhinweise** in Bernstein mit Warnzeichen statt im gedämpften Grau,
  „App komplett beenden" als roter Knopf.
- **Akzentfarbe:** drei Stellen hatten das Orange fest verdrahtet und blieben
  korallenrot, wenn du eine andere Farbe wählst.
- **Folgenhinweise erscheinen nur im betroffenen Fall.** „Ohne Vorwarnung
  merkst du es erst bei 100 %" steht nur da, wenn die Vorwarnung aus ist; „Das
  X beendet die App jetzt wirklich" nur, wenn „Im Hintergrund weiterlaufen"
  aus ist. Vorher standen beide immer da und warnten vor etwas, das gar nicht
  eingestellt war.

---

## Firmware (Clawdmeter) — schon auf dem Gerät

- **Alle 15 Animationen sind jetzt bildgleich mit dem Desktop.** Beide Seiten
  stammen aus derselben Quelle, aber aus verschiedenen Ständen: unter gleichem
  Namen lagen verschiedene Bilder. `work coding` hatte auf dem Desktop 18
  Einzelbilder, in der Firmware 23; `expression sleep` 5 gegen 24. Pixel für
  Pixel verglichen und bestätigt.
- **Alle 18 in der Auto-Rotation.** Die fünf aus dem Session Browser standen
  in keiner Auslastungsgruppe und tauchten nie von allein auf — nur über die
  PWR-Taste.
- **Abstecher auf die Zahlen:** alle 5 Minuten für eine Minute der
  Usage-Screen, dann zurück. Bricht ab, sobald du selbst umschaltest; im
  Schlaf läuft die Uhr nicht mit.
- **Neuaufbau nach Screenwechsel repariert.** LVGL färbt beim Wiederanzeigen
  schwarz, deshalb wurde der volle Neuaufbau verschoben — nur schafft ein
  Durchlauf auf diesem Panel nicht den ganzen Bildschirm (24 Streifen, dem C6
  fehlt der Speicher für mehr). Clawd wurde zwischen zwei Streifen gemalt und
  teilweise wieder überdeckt; danach werden nur geänderte Zellen nachgezogen,
  also blieb der Schaden stehen. Jetzt wird auf das Ende eines Durchlaufs
  gewartet, mit Notbremse nach 250 ms.
- **90°-Drehung in Scheiben.** Der Rückfall auf ungedrehtes Zeichnen bei
  grossen Flächen liess breite Bereiche seitwärts stehen.

---

## Was ich nicht prüfen konnte

- **Die Oberfläche mit Augen.** Solange die installierte App läuft, kann ich
  keine zweite Instanz starten; alles Sichtbare ist nur im Code geprüft.
- **Der Akkustand am echten Gerät** (siehe oben).
- **Wie lange dein Clawdmeter selbst zum Verbinden braucht** — dafür müsste
  ich das Gerät belegen dürfen.

## Offene Punkte

- Werkzeuge, die länger als 30 Sekunden laufen, gelten weiter als Rückfrage.
  Richtige Lösung: Claude-Code-Hooks.
- Clawds Hintergrund ist beim Rahmen „Aus" nicht durchsichtig — er malt seinen
  Hintergrund deckend, obwohl das Fenster einen Chroma-Key hat.
