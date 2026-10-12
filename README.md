# LEGO-Mosaik-Service

Lokaler Service, der aus einem Bild ein **baubares LEGO-Mosaik aus echten Teilen** erzeugt:

1. Das Bild wird auf eine Größe gebracht: als Format (z. B. `quadrat` = 3 × 3 LEGO-Art-Paneele à 16 × 16 Noppen, `weltkarte` = 8 × 5) oder frei in Noppen. Freie Größen rasten auf ganze Paneele ein, damit der Unterbau immer genau passt.
2. Die Farben werden per Dithering auf echte LEGO-Farben reduziert.
3. Die Palette besteht nur aus Farben, die es wirklich als 1×1-Teil gibt (Rebrickable-Daten).
4. Gleichfarbige Flächen werden zu möglichst wenigen, real existierenden Plates und Tiles zusammengefasst.
5. Optional kommen Formteile dazu, inspiriert von LEGO Art und Brick Sketches: Viertelkreise, Halbkreise, runde Tiles, Dreieck-Tiles und Cut-Corner-Wedges an Konturen, oder das ganze Bild aus runden „Punkten“ bzw. „Noppen“. Ein Relief hebt Motiv oder helle bzw. dunkle Bereiche auf Plates an.
6. Der Unterbau hält alles zusammen wie bei LEGO Art: Paneele mit Technic-Pins verbunden, darauf ein (optional erhöhter) Rahmen.
7. Ausgaben entstehen:
   - Vorschau
   - LDraw-Datei für Bricklink Studio
   - Teilelisten (BrickLink, Rebrickable, LEGO)
   - Bauanleitung im LEGO-Stil als PDF, vom Unterbau über den Rahmen bis zum fertigen Bild

Bedienung über eine deutsche Kommandozeile (auch interaktiv mit Menü) oder eine lokale HTTP-API. Alle nutzen dieselbe Pipeline.

## Setup

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Python 3.15. uv lädt Python bei Bedarf selbst.

```bash
uv sync                                  # Abhängigkeiten installieren
uv run lego-mosaik teile-aktualisieren   # Rebrickable-Daten laden (ca. 2 MB)
uv run lego-mosaik erstellen katze.jpg   # erstes Mosaik
```

Optional, damit `lego-mosaik` global im Terminal verfügbar ist:

```bash
uv tool install --editable .
```

Die Pfade `daten/` und `ausgabe/` gelten relativ zum aktuellen Ordner. Der Befehl muss deshalb im Projektordner aufgerufen werden.

**Bricklink Studio (empfohlen, optional):** Ist Studio installiert (macOS: `/Applications/Studio 2.0`), liest der Service dessen Farbtabelle. Damit stimmen die LDraw-Farbcodes sicher, es gibt BrickLink-Farbcodes für die Wanted List, offizielle LEGO-Farbnamen und Farbkategorien (siehe [Daten und Annahmen](#daten-und-annahmen)).

## Befehle

| Befehl | Zweck |
| --- | --- |
| `lego-mosaik erstellen BILD` | Mosaik erzeugen |
| `lego-mosaik interaktiv [BILD]` | Menügeführt: Bild laden, Optionen einstellen, Vorschau im Terminal, Mosaik ausgeben |
| `lego-mosaik teile-aktualisieren [--erzwingen]` | Rebrickable-CSV-Dateien laden |
| `lego-mosaik farben` | Palette als Tabelle mit Farbfeld anzeigen |
| `lego-mosaik formate` | Formate mit Größe in Noppen |
| `lego-mosaik verfahren` | Registrierte Dithering-, Zerlegungs-, Stil- und Ausgabe-Namen |
| `lego-mosaik server [--host] [--port]` | HTTP-API starten (Standard `127.0.0.1:8000`) |

Hilfe gibt es mit `-h`, `--hilfe` oder `--help`, auch für jeden Befehl (`lego-mosaik erstellen --hilfe`). `--debug` vor dem Befehl zeigt ausführliche Logs und Tracebacks.

Optionen von `erstellen` (in der Hilfe nach Gruppen sortiert):

| Option | Bedeutung |
| --- | --- |
| **Größe und Unterbau** | |
| `--unterbau art\|grundplatte\|ohne` | LEGO-Art-Paneele (mit Pins verbunden), lose Grundplatten oder nichts |
| `--plattengroesse 16\|32\|48` | Größe einer Platte in Noppen (Art-Paneel: 16) |
| `--format quadrat` | Format aus `lego-mosaik formate` (statt `--platten`) |
| `--platten 3x3` | Anzahl Platten (Breite × Höhe) |
| `--breite 50` / `--hoehe 70` | Eigene Größe in Noppen statt Platten, rastet auf ganze Platten ein; nur eine Seite → die andere aus dem Seitenverhältnis |
| `--unterbaufarbe 0` | Farbe der Paneele bzw. Grundplatten (Rebrickable-ID) |
| **Rahmen** | |
| `--rahmen 1` / `--rahmenfarbe 0` | Rahmenbreite in Noppen (0 = ohne) und Farbe |
| `--eckenfarbe 15` | Eigene Farbe für die vier Ecken |
| `--rahmen-teile tile` | Teilarten des Rahmens (glatt mit `tile`) |
| `--rahmen-erhoehung 1` | Rahmen um so viele Plates höher als das Bild (0 = bündig) |
| **Bild und Farben** | |
| `--modus zuschneiden\|einpassen` | Bild mittig zuschneiden oder ganz einpassen |
| `--kontrast 1.2` / `--saettigung 1.1` | Bild vorher anpassen (1.0 = unverändert) |
| `--teile plate,tile` | Erlaubte Teilarten des Bilds (`brick` nur allein) |
| `--farbkategorien solid,pearl` | Erlaubte Farbkategorien |
| `--exotische-farben` | Alle Kategorien außer Gummi (Metallic, Pearl, Chrome, Transparent, …) |
| `--max-farben 12` | Höchstens so viele Farben (0 = alle) |
| **Stil und Relief** | |
| `--stil quadrat\|formen\|punkte\|noppen` | Nur eckige Teile, Formteile an Konturen, alles runde Tiles oder alles runde Plates |
| `--relief ohne\|motiv\|hell\|dunkel` | Motiv erhöht (Hintergrund flach) bzw. helle oder dunkle Farben höher |
| `--relief-hoehe 2` | Höchste Relief-Stufe in Plates (1–3) |
| **Verfahren und Ausgaben** | |
| `--dithering atkinson` / `--staerke 0.8` | Verfahren und Stärke |
| `--farbabstand cie76\|ciede2000` | Formel für den Farbabstand (schnell bzw. genau) |
| `--zerlegung greedy` | Zerlegungsverfahren |
| `--ausgaben preview_png,ldraw` | Nur bestimmte Ausgaben |
| `--ziel ordner/` | Ausgabeordner |

Beispiele:

```bash
uv run lego-mosaik erstellen katze.jpg --platten 3x3 --rahmen-erhoehung 1 --eckenfarbe 15
uv run lego-mosaik erstellen katze.jpg --format quer --stil formen --relief motiv
uv run lego-mosaik erstellen katze.jpg --format quadrat --stil punkte   # Look wie LEGO Art
uv run lego-mosaik erstellen katze.jpg --breite 64            # Höhe aus dem Seitenverhältnis
uv run lego-mosaik erstellen katze.jpg --unterbau grundplatte --plattengroesse 48 --platten 1x1
uv run lego-mosaik erstellen katze.jpg --exotische-farben --dithering atkinson
uv run lego-mosaik interaktiv katze.jpg
uv run lego-mosaik farben --teile tile --farbkategorien solid,pearl
uv run lego-mosaik teile-aktualisieren --erzwingen
```

### Interaktiver Modus

`lego-mosaik interaktiv` öffnet ein Menü im Terminal:

| Taste | Aktion |
| --- | --- |
| `1` | Bild laden (Pfad eingeben; Größe, Format und Seitenverhältnis werden angezeigt, dazu passende Formate) |
| `2`–`8` | Größe und Unterbau, Rahmen, Bild anpassen, Farben und Teile, Stil und Relief, Verfahren, Ausgaben |
| `9` | Alle wirksamen Einstellungen als Tabelle |
| `10` | Vorschau im Terminal: das Mosaik als farbige Blöcke, ohne Dateien zu schreiben |
| `11` | Mosaik erstellen und alle Dateien ausgeben |
| `12` | Farbpalette für die aktuellen Teilarten und Farbkategorien |
| `13` | Formate mit Größe in Noppen |
| `0` | Beenden (auch Strg+D) |

Bei jeder Frage steht der aktuelle Wert dabei. Enter behält ihn, `-` setzt den Standard aus `CONFIG` zurück. Format und eigene Größe schließen sich aus: Wer ein Format wählt, verwirft die eigene Größe und umgekehrt. Ungültige Angaben werden mit einer deutschen Meldung abgelehnt; die vorherigen Einstellungen bleiben dann gültig. Das Bild kann auch direkt mitgegeben werden: `lego-mosaik interaktiv katze.jpg`.

Exit-Codes: `0` Erfolg, `1` Verarbeitungsfehler (z. B. Download), `2` ungültige Eingabe.

## Konfiguration

Alle Standardwerte stehen **an einer Stelle**: `CONFIG` in `src/lego_mosaik/main.py` (öffnen mit `nvim src/lego_mosaik/main.py`). CLI-Optionen und API-Felder überschreiben einzelne Werte nur für einen Lauf.

| Bereich | Wichtige Werte (Standard) |
| --- | --- |
| `layout` | Unterbau LEGO-Art-Paneele (16 × 16), 3 × 3 Platten = 48 × 48 Noppen, freie Größe aus, 2 Pins pro Naht |
| `frame` | 1 Noppe Schwarz, Ecken wie Rahmen, nur Tiles, eine Plate erhöht |
| `palette` | Teilarten Plate + Tile, Kategorie `solid`, `include_color_ids`, `exclude_color_ids`, `max_colors` |
| `shapes` | Stil `quadrat`; für `formen`: erlaubte Formteile, 6 Teilpixel pro Noppe, Mindestgewinn 20 % und ΔE 3, Unterlagen erlaubt |
| `relief` | aus; Stufen 1, Farbe der Stützlagen wie der Unterbau |
| `dithering` | `floyd_steinberg`, Stärke 1.0, CIEDE2000, Serpentine |
| `tiling` | `greedy`, längste Teilseite 8, Zerlegung pro Abschnitt |
| `outputs` | `preview_png`, `ldraw`, `parts_list_csv`, `official_parts_lists`, `instructions_png`, `result_json` |
| `instructions` | Abschnitte 16 × 16, A4-Seiten, Kamera (60° Höhe, 0° Drehung), Größe der Schrittansicht, Schriftenliste |
| `source` | Rebrickable-URL, Höchstalter 7 Tage, automatische Aktualisierung, Pfad zur Studio-Farbtabelle |
| `server` | `127.0.0.1:8000`, Upload bis 20 MB, Katalogprüfung alle 24 h |

**Exotische Farben:**
- `color_categories` legt fest, welche Oberflächen erlaubt sind: `solid`, `transparent`, `metallic`, `pearl`, `chrome`, `glitter`, `speckle`, `satin`, `milky`, `rubber`.
- Einzelne Farben lassen sich mit `include_color_ids` trotzdem erlauben oder mit `exclude_color_ids` sperren.

**Größe:**
- Entweder ein Format, Platten × Plattengröße oder frei in Noppen (`width_studs`, `height_studs`), jeweils 8 bis 256 Noppen pro Seite.
- Formate (`lego-mosaik formate`): `mini` 1 × 1, `klein` 2 × 2, `quadrat` 3 × 3 (wie die meisten LEGO-Art-Sets), `gross` 4 × 4, `riesig` 5 × 5, `quer` 4 × 3, `hoch` 3 × 4, `breit` 6 × 4, `panorama` 6 × 3, `weltkarte` 8 × 5 (wie LEGO Art 31203). Angegeben in Platten; mit Art-Paneelen ist `quer` also 64 × 48 Noppen.
- Freie Größen rasten mit Unterbau auf ganze Platten ein (kaufmännisch gerundet: 40 → 48, 23 → 16). So steht nie ein Paneel über und keins fehlt. Ohne Unterbau (`--unterbau ohne`) bleibt die Größe exakt.
- Fehlt bei freier Größe eine Seite, bekommt die Bildfläche innerhalb des Rahmens das Seitenverhältnis des Bilds (EXIF-Drehung berücksichtigt); danach rastet auch sie ein.
- Der interaktive Modus schlägt nach dem Laden die Formate vor, deren Seitenverhältnis am besten zum Bild passt.

**Stil (`shapes`):**
- `quadrat` (Standard): nur eckige Plates und Tiles.
- `formen`: Das Bild wird zusätzlich feiner ausgewertet (6 × 6 Teilpixel pro Noppe). An Konturen setzt der Service Tile 1 × 1 Quarter Circle (25269), Tile 1 × 1 Half Circle (24246), Tile Round 1 × 1 (98138), Tile 2 × 2 Triangular (35787) und Wedge Plate 2 × 2 Cut Corner (26601) in der passenden Drehung, wenn sie die Kontur deutlich genauer treffen als jede volle Noppe. In den Lücken ist der Untergrund zu sehen. Mit `underlay` (Standard) darf ein Formteil auf einer Plate in zweiter Farbe liegen, wie die Plate-Stapel bei Brick Sketches: Dann zeigt die Lücke z. B. die Nachbarfarbe, und das Formteil steht eine Plate höher.
- `punkte`: jede Noppe ein Tile Round 1 × 1, wie LEGO Art. Die Palette enthält dann nur Farben, in denen es dieses Teil gibt.
- `noppen`: jede Noppe eine Plate Round 1 × 1 („Studs“, BrickLink 4073).
- Formteile brauchen einen Unterbau (er ist in den Lücken zu sehen) und gehen nicht mit Bricks.

**Relief (`relief`):**
- `motiv`: Der Hintergrund (häufigste Randfarbe, soweit vom Rand aus zusammenhängend) bleibt flach, alles andere liegt `levels` Plates höher.
- `hell` / `dunkel`: Helle bzw. dunkle Farben liegen höher, in `levels` + 1 gleich großen Stufen.
- Unter erhöhten Teilen liegen Stützlagen aus Plates (Standard: Farbe des Unterbaus). Ein Bildteil reicht nie über eine Stufe; 2 × 2-Formteile liegen nur auf gleich hohen Noppen.

**Unterbau (`substrate`):**
- `ART_PANEL` (Standard): LEGO-Art-Paneele „Brick 16 x 16 x 1 1/3 with Pin Holes“ (65803). Sie haben Technic-Löcher an den Seiten; an jeder Naht stecken `pins_per_seam` Technic-Pins (2780). So hält das ganze Bild zusammen.
- `BASEPLATE`: dünne Grundplatten 3867 (16 × 16), 3811 (32 × 32) und 4186 (48 × 48). Mehrere davon lassen sich **nicht** miteinander verbinden; nur Rahmen und Teile über den Nähten halten sie. Die Pipeline warnt deshalb bei mehr als einer Grundplatte.
- `NONE`: ohne Unterbau.
- Ohne `substrate_color_id` gilt: Paneele Black, Grundplatten Light Bluish Gray. Ob es das Teil in der Farbe gibt, wird gegen die Rebrickable-Daten geprüft.

**Rahmen (`frame`):**
- Der Rahmen liegt auf dem äußeren Ring; das Bild füllt die Fläche innen.
- `corner_color_id`: eigene Farbe für die Ecken (`width` × `width` Noppen).
- `part_kinds`: eigene Teilarten, z. B. nur `TILE` für einen glatten Rahmen wie bei LEGO Art.
- `raise_plates`: hebt den Rahmen um so viele Plates über das Bild. Darunter liegen Plates in Rahmenfarbe, „auf Lücke“ verlegt (Mauerverband): Ihre Fugen liegen nie auf einer Naht des Unterbaus, sie verbinden die Platten also zusätzlich am Rand.

## Ausgaben

Jeder Lauf schreibt in einen eigenen Ordner, z. B. `ausgabe/2026-10-12_143005_katze/`:

| Name | Datei(en) | Inhalt |
| --- | --- | --- |
| `preview_png` | `vorschau.png` | Draufsicht mit Noppen und Teilekanten |
| `ldraw` | `mosaik.ldr` | Modell für Bricklink Studio, Bauschritte je Abschnitt |
| `parts_list_csv` | `teileliste.csv`, `teileliste_rebrickable.csv` | Eigene Liste (Semikolon, für Excel) und Rebrickable-Import |
| `official_parts_lists` | `teileliste_bricklink.xml`, `teileliste_lego.csv` | BrickLink Wanted List und Liste mit LEGO-Element-IDs |
| `instructions_png` | `anleitung_01.png` …, `anleitung.pdf` | Bauanleitung im LEGO-Stil mit 3D-Renderbildern (siehe unten) |
| `result_json` | `ergebnis.json` | Konfiguration, Statistik, Palette, Dateiliste |

## Bauanleitung

`anleitung.pdf` entsteht vollautomatisch und ohne externe Programme. Gerendert wird mit einem eigenen kleinen 3D-Renderer auf Basis von Pillow (`outputs/iso_render.py`). Gebaut wird wie bei LEGO Art: erst die stabile Grundlage, dann das Bild.

1. **Titelseite:** das fertige Mosaik in 3D, Kennzahlen und eine Karte der Abschnitte
2. **Teileübersicht:** alle Teile als gerenderte Einzelteile mit Stückzahl und Farbname (inklusive Paneele und Pins)
3. **Unterbau auslegen:** Paneele bzw. Grundplatten, die Nähte sind eingezeichnet
4. **Paneele verbinden:** Verbindungsplan mit nummerierten Paneelen; orange Balken zeigen, wo die Technic-Pins stecken
5. **Rahmen:** erst die Stützlage(n) unter einem erhöhten Rahmen, dann der sichtbare Rahmen
6. **Pro Abschnitt** (16 × 16, also genau ein Paneel):
   - Fortschrittskarte: schon gebaute Abschnitte farbig, offene grau, der nächste rot umrandet
   - zuerst die Relief-Lagen und Unterlagen der Formteile („Relief, Lage 1“), von unten nach oben
   - pro Bauschritt ein großer Schrittzähler, der hellblaue Teile-Kasten („3×“ unter jedem Teilbild) und die 3D-Ansicht des Abschnitts mit den neuen Teilen orange umrandet; Formteile erscheinen mit ihrem Umriss und ihrer Drehung
7. **Fertig:** das ganze Mosaik und was es zusammenhält

**Kamera:** Sie schaut gerade von vorne und steil von oben (wie bei LEGO Art). Zeilen und Spalten bleiben dadurch gerade und abzählbar. Einstellbar ist das über `view_elevation_deg` und `view_azimuth_deg` in `CONFIG.instructions`: Ein Azimut über 0° dreht die Ansicht und zeigt zusätzlich die Seitenflächen.

**Farben:** Bereits gebaute Teile behalten ihre echte Farbe. Ausgeblasste Farben würden wie andere LEGO-Farben aussehen.

**Schritte:** Die Bauschritte sind exakt dieselben wie in `mosaik.ldr`.

### Optional: Bricklink Studio

`mosaik.ldr` lässt sich in Studio über *File › Import › Import Model …* öffnen. Studio übernimmt die Bauschritte. Im Instruction-Modus (Button *Instruction* bzw. *Tool › Instruction Maker*) kann man daraus ebenfalls ein PDF exportieren (*Export* oben rechts). Die eigene Anleitung ist dafür aber nicht nötig.

Zur Ausrichtung: `scripts/orientierung.py` erzeugt kleine Testmodelle (`ausgabe/orientierung/`), darunter `formteile/` mit jedem Formteil in vier Drehungen und einer Vorschau zum Vergleich. `scripts/ldraw_achsen_pruefen.py` prüft die Teileausrichtung automatisch gegen die LDraw-Bibliothek von Studio.

## Teile bestellen

- **BrickLink:** `teileliste_bricklink.xml` unter *Want › Upload* hochladen. Teilenummern und Farbcodes sind BrickLink-Werte, z. B. Tile 1 × 1 = `3070`, nicht Rebrickables `3070b`.
- **Rebrickable:** `teileliste_rebrickable.csv` (`Part,Color,Quantity`) über den CSV-Import einer Teileliste.
- **LEGO Pick a Brick:** `teileliste_lego.csv` enthält Element-IDs und offizielle LEGO-Farbnamen zum Suchen. Ob ein Element dort verfügbar ist, wird nicht geprüft.

## HTTP-API

```bash
uv run lego-mosaik server        # danach http://127.0.0.1:8000/docs im Browser
```

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/gesundheit` | Status und Stand des Teilekatalogs |
| `GET` | `/farben?teile=plate,tile&exotisch=false` | Palette |
| `GET` | `/verfahren` | Registrierte Verfahren und Ausgaben |
| `POST` | `/mosaike` | Multipart: Datei `bild` plus optionale Felder wie bei `erstellen` (z. B. `unterbau`, `format`, `breite`, `stil`, `relief`, `relief_hoehe`, `eckenfarbe`, `rahmen_erhoehung`) → `201` mit ID, Statistik, Download-URLs |
| `GET` | `/mosaike/{id}` | Inhalt von `ergebnis.json` |
| `GET` | `/mosaike/{id}/dateien/{dateiname}` | Datei herunterladen (nur Namen aus `ergebnis.json`) |
| `POST` | `/teile/aktualisieren?erzwingen=false` | Katalog aktualisieren |

```bash
curl -F bild=@katze.jpg -F format=quadrat -F stil=formen -F relief=motiv http://127.0.0.1:8000/mosaike
```

Fehler kommen als `{"fehler": "…"}` mit `400` (kein Bild), `404`, `413` (zu groß) oder `422` (ungültige Angaben).

> **Sicherheit:** Die API hat **keine Authentifizierung**. Sie bindet deshalb standardmäßig nur an `127.0.0.1`. Bitte nicht öffentlich erreichbar machen.

## Katalog regelmäßig aktualisieren

Im Server-Modus prüft ein Hintergrund-Task den Katalog beim Start und danach alle 24 Stunden. Ohne Server übernimmt das ein **systemd-User-Timer** (Linux):

`~/.config/systemd/user/lego-mosaik-update.service`

```ini
[Unit]
Description=LEGO-Mosaik: Rebrickable-Teilekatalog aktualisieren

[Service]
Type=oneshot
WorkingDirectory=%h/projekte/lego-mosaik
ExecStart=%h/.local/bin/uv run lego-mosaik teile-aktualisieren
```

`~/.config/systemd/user/lego-mosaik-update.timer`

```ini
[Unit]
Description=LEGO-Mosaik: Katalog täglich aktualisieren

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
nvim ~/.config/systemd/user/lego-mosaik-update.service   # Pfade anpassen
systemctl --user daemon-reload
systemctl --user enable --now lego-mosaik-update.timer
systemctl --user list-timers                              # prüfen
journalctl --user -u lego-mosaik-update.service           # Logs
```

`Persistent=true` holt einen verpassten Lauf nach, z. B. wenn der Rechner aus war. Unter macOS erfüllt ein `launchd`-Agent (`~/Library/LaunchAgents/`) denselben Zweck.

## Architektur

```text
src/lego_mosaik/
├── main.py            Composition Root: CONFIG + Verdrahtung (einzige Stelle mit konkreten Klassen)
├── config.py          Dataclasses der Konfiguration, OverrideInput → MosaicOverrides, Validierung
├── registry.py        Name → Fabrikfunktion für Dithering, Zerlegung, Ausgaben
├── pipeline.py        Orchestrierung; kennt nur Protocols und Domain
├── domain/            Modelle, Palette, Layout (Formate, Größe, Unterbau, Pins, Rahmen), Formteil-Umrisse, Relief, Bauschritte, Fehler
├── sources/           Rebrickable-CSV, Studio-Farbtabelle, Updater (+ protocol.py)
├── imaging/           Laden, Einpassen, Farbraum Lab, Farbsuche (+ protocol.py)
├── dithering/         nearest, error_diffusion (Floyd-Steinberg, Atkinson, Sierra Lite), ordered
├── tiling/            single_stud, greedy (+ protocol.py)
├── shaping/           Stile: plain (quadrat), contour (formen), dots (punkte, noppen) (+ protocol.py)
├── outputs/           Vorschau, LDraw, Teilelisten, offizielle Listen, 3D-Renderer, Pläne, Anleitung, JSON
└── interfaces/        cli.py (Typer), interactive.py (Menü), display.py (Rich-Ausgaben), api.py (FastAPI)
```

**Ablauf:** Katalog laden → Palette, Farben und Unterbau prüfen → Größe bestimmen (Format, ggf. aus dem Seitenverhältnis, auf ganze Platten eingerastet) → Bild einpassen (für `formen` zusätzlich feiner) → nach Lab umrechnen → dithern (bei `max_colors` zweimal) → Rahmen samt Ecken einsetzen → Relief-Höhen bestimmen → Formteile pro Abschnitt wählen → übrige Noppen pro Abschnitt und Höhe zerlegen, Rahmen am Stück, Stützlagen → Unterbau und Pins platzieren → alle Writer (`result_json` zuletzt).

**Lagen im Mosaik:** `Mosaic` trennt Unterbau, Verbinder, Stützteile, Rahmen, Relief (Stützlagen und Unterlagen) und Bild. `domain/sections.py` macht daraus die Bauschritte (`assembly_steps`); LDraw-Datei und Anleitung nutzen genau diese Reihenfolge.

**DRY bei den Oberflächen:** CLI, interaktiver Modus und API füllen alle dasselbe `OverrideInput` (rohe Texte und Zahlen). `parse_overrides` übersetzt es an genau einer Stelle in `MosaicOverrides`, `apply_overrides` prüft das Ergebnis.

**Abhängigkeitsregel:**
- `domain` hängt von nichts ab.
- Fachmodule kennen nur `domain` und ihr eigenes Protocol.
- `pipeline.py` kennt nur Protocols.
- Nur `main.py` kennt konkrete Klassen.
- `interfaces/` rufen ausschließlich die Pipeline auf.
- Wo ein Fachmodul etwas aus einem anderen braucht, gibt es ein Protocol. Beispiel: `ColorSpace` in `dithering/protocol.py` wird von `imaging/color_space.py` erfüllt (Dependency Inversion).

**Patterns:**
- Composition Root: `main.py`
- Strategy: Dithering, Zerlegung, Stil (Formwahl), Writer
- Null Object: Stil `quadrat` (`PlainShaper`) setzt keine Formteile, die Pipeline verzweigt nie
- Registry + Factory: `registry.py`
- Protocol (strukturelle Typisierung)
- Facade: `PillowImagePreparer`
- Builder: Anleitung aus Einheiten zusammensetzen, die nie über Seiten getrennt werden
- Facade + Lazy Initialization: `pipeline.preview()` rechnet ohne Dateien, die Pipeline im Menü entsteht erst beim ersten Zugriff
- Table-Driven Design: die Menüfragen stehen als Daten (`GROUPS` in `interactive.py`), eine Funktion fragt sie alle ab
- Painter's Algorithm: 3D-Verdeckung durch Zeichnen von hinten nach vorne
- Dependency Injection: Konstruktorparameter statt Konfigurationsobjekt
- Context Manager: Zeitmessung pro Schritt

**Erweitern:** Ein neues Dithering-Verfahren braucht eine neue Klasse mit `dither(image_lab, palette_lab)` und eine Zeile in `DITHERERS`. Sonst ändert sich nichts.

**Lazy Imports (PEP 810):** NumPy, scikit-image, Pillow, httpx, FastAPI und uvicorn werden mit `lazy import` geladen. `lego-mosaik --hilfe` startet in etwa 0,2 s, ohne sie zu laden (Test: `tests/test_lazy_imports.py`; messen mit `uv run python -X importtime -c "import lego_mosaik.main"`).

## Daten und Annahmen

- **Quelle:** Rebrickable-Downloads `colors.csv.gz`, `parts.csv.gz` und `elements.csv.gz`, gelesen über die Spaltennamen. Die Sonderfarben `-1` und `9999` sind ausgeschlossen.
- **Whitelist:** Nur Teile aus `daten/teil_geometrie.csv` werden verwendet. Die Tabelle enthält Maße, LDraw-Datei, Höhe, Achse der langen Seite und die BrickLink-Nummer.
- **LDraw-Farbcodes:** Rebrickable-IDs entsprechen **nicht immer** den LDraw-Codes. Beispiel: Rebrickable 326 „Olive Green“ ist LDraw 330, LDraw 326 ist „Yellowish Green“. Deshalb gilt:
  1. Override aus `daten/farb_zuordnung.csv`
  2. Namensabgleich mit Studios Farbtabelle
  3. ohne Studio: Rebrickable-ID (ungeprüft, mit Warnung)

  Ist die Studio-Tabelle vorhanden und findet sich kein Treffer, wird die Farbe nicht verwendet, weil sie in Studio falsch erscheinen würde. Das betrifft einige sehr neue Farben.
- **Palette:** Eine Farbe kommt nur hinein, wenn es ein 1×1-Teil der erlaubten Teilarten in ihr gibt.
- **Draufsicht:** Es dürfen nur Teilarten gleicher Höhe gemischt werden (Plate + Tile; Brick nur allein).
- **Formteile:** Die Umrisse und die Grundausrichtung (`domain/shapes.py`) sind aus Studios LDraw-Bibliothek gemessen; die Dreieck-Schenkel sind 1,85 Noppen lang (37 LDU). 25269 liegt in Studio unter `UnOfficial`, wird aber normal geladen. Ob eine Form gewählt wird, entscheidet der quadrierte Lab-Abstand (CIE76) der Teilpixel, unabhängig von `--farbabstand`.
- **LEGO-Art-Paneel und Pins (Annahmen, bitte in Studio prüfen):** 65803 ist 32 LDU hoch, der Ursprung der LDraw-Datei liegt wie bei Plates an der Oberkante. Der Pin 2780 liegt in LDraw entlang z (`ldraw_long_axis`), seine Mitte sitzt `connector_depth_ldu` = 16 LDU unter der Paneel-Oberkante. Die Pins sind gleichmäßig auf jede Naht verteilt (bei 2 Pins: Noppe 4 und 12). Die genaue Lage der Seitenlöcher von 65803 habe ich nicht verlässlich gefunden. `scripts/orientierung.py` erzeugt dafür `art_paneele/mosaik.ldr`, und `scripts/ldraw_achsen_pruefen.py` prüft die Pin-Achse gegen Studios LDraw-Bibliothek.

## Entwicklung

```bash
uv run pytest                                  # alle Tests (offline)
uv run pytest --golden-aktualisieren           # Golden Files bewusst neu erzeugen
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run python scripts/ldraw_achsen_pruefen.py  # Teileausrichtung gegen LDraw prüfen
```

Die Tests in `tests/test_studio_reference.py` gleichen BrickLink-Nummern, Teileachsen und die Umrisse der Formteile (in allen vier Drehungen) mit einer lokalen Studio-Installation ab. Ohne Studio werden sie übersprungen.

## Grenzen

- Draufsicht mit höchstens drei Relief-Stufen; ein stehendes Mosaik aus gestapelten Bricks wäre eine weitere Strategie.
- Formteile setzt der Stil `formen` nur dort, wo sie klar besser sind; Jumper-Versatz (halbe Noppen) und gewinkelte Teile wie bei Brick Sketches gibt es nicht.
- Lose Grundplatten (`--unterbau grundplatte` mit mehreren Platten) halten nur über Rahmen und Teile an den Nähten zusammen; stabil ist der Art-Unterbau.
- Kein Aufhänger (LEGO Art hat ein eigenes Element dafür) und kein Signatur-Tile.
- Die Greedy-Zerlegung findet nicht immer die minimale Teilezahl, ist aber schnell und deterministisch.
- Kein `.io`-Export, kein Preisabgleich, keine Prüfung der Verfügbarkeit bei Pick a Brick.
