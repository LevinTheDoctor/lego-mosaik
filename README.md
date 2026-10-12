# LEGO-Mosaik-Service

Lokaler Service, der aus einem Bild ein **baubares LEGO-Mosaik aus echten Teilen** erzeugt:

1. Das Bild wird auf eine Größe in Grundplatten gebracht (z. B. 2 × 2 Grundplatten à 32 × 32 Noppen).
2. Die Farben werden per Dithering auf echte LEGO-Farben reduziert.
3. Die Palette besteht nur aus Farben, die es wirklich als 1×1-Teil gibt (Rebrickable-Daten).
4. Gleichfarbige Flächen werden zu möglichst wenigen, real existierenden Plates und Tiles zusammengefasst.
5. Ausgaben entstehen:
   - Vorschau
   - LDraw-Datei für Bricklink Studio
   - Teilelisten (BrickLink, Rebrickable, LEGO)
   - Bauanleitung als PDF

Bedienung über eine deutsche Kommandozeile oder eine lokale HTTP-API. Beide nutzen dieselbe Pipeline.

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
| `lego-mosaik teile-aktualisieren [--erzwingen]` | Rebrickable-CSV-Dateien laden |
| `lego-mosaik farben` | Palette als Tabelle mit Farbfeld anzeigen |
| `lego-mosaik verfahren` | Registrierte Dithering-, Zerlegungs- und Ausgabe-Namen |
| `lego-mosaik server [--host] [--port]` | HTTP-API starten (Standard `127.0.0.1:8000`) |

Hilfe gibt es mit `-h`, `--hilfe` oder `--help`, auch für jeden Befehl (`lego-mosaik erstellen --hilfe`). `--debug` vor dem Befehl zeigt ausführliche Logs und Tracebacks.

Wichtige Optionen von `erstellen`:

| Option | Bedeutung |
| --- | --- |
| `--grundplatte 16\|32\|48` | Größe einer Grundplatte in Noppen |
| `--platten 2x2` | Anzahl Grundplatten (Breite × Höhe) |
| `--rahmen 1` / `--rahmenfarbe 0` | Rahmenbreite in Noppen (0 = ohne) und Farbe (Rebrickable-ID) |
| `--mit-grundplatte` / `--ohne-grundplatte` | Grundplatten in LDraw-Datei und Teilelisten |
| `--modus zuschneiden\|einpassen` | Bild mittig zuschneiden oder ganz einpassen |
| `--teile plate,tile` | Erlaubte Teilarten (`brick` nur allein) |
| `--farbkategorien solid,pearl` | Erlaubte Farbkategorien |
| `--exotische-farben` | Alle Kategorien außer Gummi (Metallic, Pearl, Chrome, Transparent, …) |
| `--dithering atkinson` / `--staerke 0.8` | Verfahren und Stärke |
| `--zerlegung greedy` | Zerlegungsverfahren |
| `--ausgaben preview_png,ldraw` | Nur bestimmte Ausgaben |
| `--ziel ordner/` | Ausgabeordner |

Beispiele:

```bash
uv run lego-mosaik erstellen katze.jpg --grundplatte 32 --platten 2x2 --rahmen 1
uv run lego-mosaik erstellen katze.jpg --exotische-farben --dithering atkinson
uv run lego-mosaik farben --teile tile --farbkategorien solid,pearl
uv run lego-mosaik teile-aktualisieren --erzwingen
```

Exit-Codes: `0` Erfolg, `1` Verarbeitungsfehler (z. B. Download), `2` ungültige Eingabe.

## Konfiguration

Alle Standardwerte stehen **an einer Stelle**: `CONFIG` in `src/lego_mosaik/main.py` (öffnen mit `nvim src/lego_mosaik/main.py`). CLI-Optionen und API-Felder überschreiben einzelne Werte nur für einen Lauf.

| Bereich | Wichtige Werte (Standard) |
| --- | --- |
| `layout` | Grundplatte 48 × 48, 1 × 1 Platten, Grundplatte Light Bluish Gray, Rahmen 1 Noppe Schwarz |
| `palette` | Teilarten Plate + Tile, Kategorie `solid`, `include_color_ids`, `exclude_color_ids`, `max_colors` |
| `dithering` | `floyd_steinberg`, Stärke 1.0, CIEDE2000, Serpentine |
| `tiling` | `greedy`, längste Teilseite 8, Zerlegung pro Abschnitt |
| `outputs` | `preview_png`, `ldraw`, `parts_list_csv`, `official_parts_lists`, `instructions_png`, `result_json` |
| `instructions` | Abschnitte 16 × 16, A4-Seiten, Schriftenliste |
| `source` | Rebrickable-URL, Höchstalter 7 Tage, automatische Aktualisierung, Pfad zur Studio-Farbtabelle |
| `server` | `127.0.0.1:8000`, Upload bis 20 MB, Katalogprüfung alle 24 h |

**Exotische Farben:**
- `color_categories` legt fest, welche Oberflächen erlaubt sind: `solid`, `transparent`, `metallic`, `pearl`, `chrome`, `glitter`, `speckle`, `satin`, `milky`, `rubber`.
- Einzelne Farben lassen sich mit `include_color_ids` trotzdem erlauben oder mit `exclude_color_ids` sperren.

**Größe und Rahmen:**
- Die Gesamtgröße ist Grundplatten × Plattengröße (höchstens 256 Noppen pro Seite).
- Der Rahmen liegt auf dem äußeren Ring; das Bild füllt die Fläche innen.
- Die Grundplatten 3867 (16 × 16), 3811 (32 × 32) und 4186 (48 × 48) stehen in `daten/teil_geometrie.csv`. Ob es sie in der gewählten Farbe gibt, wird gegen die Rebrickable-Daten geprüft.

## Ausgaben

Jeder Lauf schreibt in einen eigenen Ordner, z. B. `ausgabe/2026-10-12_143005_katze/`:

| Name | Datei(en) | Inhalt |
| --- | --- | --- |
| `preview_png` | `vorschau.png` | Draufsicht mit Noppen und Teilekanten |
| `ldraw` | `mosaik.ldr` | Modell für Bricklink Studio, Bauschritte je Abschnitt |
| `parts_list_csv` | `teileliste.csv`, `teileliste_rebrickable.csv` | Eigene Liste (Semikolon, für Excel) und Rebrickable-Import |
| `official_parts_lists` | `teileliste_bricklink.xml`, `teileliste_lego.csv` | BrickLink Wanted List und Liste mit LEGO-Element-IDs |
| `instructions_png` | `anleitung_01.png` …, `anleitung.pdf` | Übersicht, Farbliste, eine Seite pro 16 × 16-Abschnitt |
| `result_json` | `ergebnis.json` | Konfiguration, Statistik, Palette, Dateiliste |

## Bricklink Studio: Import und gerenderte Anleitung

Studio hat keine Kommandozeile. Die gerenderte PDF-Anleitung entsteht deshalb per Klick:

1. Studio öffnen und `mosaik.ldr` über *File › Import › Import Model …* laden (oder direkt öffnen).
2. In den Instruction-Modus wechseln: Button *Instruction* in der Mitte der Werkzeugleiste oder *Tool › Instruction Maker*.
3. Im *Step Editor* die Bauschritte prüfen. Schritt 1 sind die Grundplatten, danach folgt pro Abschnitt (16 × 16) ein Schritt je vier Zeilen.
   > Bitte beim ersten Import prüfen, ob Studio die `0 STEP`-Zeilen als Schritte übernimmt. Die Studio-Doku sagt dazu nichts Verbindliches.
4. In *Page Design* das Layout anpassen und über *Export* oben rechts als PDF speichern. Bei großen Modellen kann man im Bereich *Pages* nur einen Teil der Seiten exportieren.

Zusätzlich erzeugt der Service immer eine eigene `anleitung.pdf`, ganz ohne Studio. Ihre Abschnitte haben dieselbe Reihenfolge wie die Bauschritte in `mosaik.ldr`.

Zur Ausrichtung: `scripts/orientierung.py` erzeugt ein kleines Testmodell (`ausgabe/orientierung/`), das man mit der beiliegenden Vorschau vergleichen kann. `scripts/ldraw_achsen_pruefen.py` prüft die Teileausrichtung automatisch gegen die LDraw-Bibliothek von Studio.

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
| `POST` | `/mosaike` | Multipart: Datei `bild` plus optionale Felder wie bei `erstellen` → `201` mit ID, Statistik, Download-URLs |
| `GET` | `/mosaike/{id}` | Inhalt von `ergebnis.json` |
| `GET` | `/mosaike/{id}/dateien/{dateiname}` | Datei herunterladen (nur Namen aus `ergebnis.json`) |
| `POST` | `/teile/aktualisieren?erzwingen=false` | Katalog aktualisieren |

```bash
curl -F bild=@katze.jpg -F platten=2x2 -F grundplatte=32 http://127.0.0.1:8000/mosaike
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
├── config.py          Dataclasses der Konfiguration, MosaicOverrides, Validierung, Text-Parser
├── registry.py        Name → Fabrikfunktion für Dithering, Zerlegung, Ausgaben
├── pipeline.py        Orchestrierung; kennt nur Protocols und Domain
├── domain/            Modelle, Palette, Layout (Grundplatten/Rahmen), Abschnitte, Fehler
├── sources/           Rebrickable-CSV, Studio-Farbtabelle, Updater (+ protocol.py)
├── imaging/           Laden, Einpassen, Farbraum Lab, Farbsuche (+ protocol.py)
├── dithering/         nearest, error_diffusion (Floyd-Steinberg, Atkinson, Sierra Lite), ordered
├── tiling/            single_stud, greedy (+ protocol.py)
├── outputs/           Vorschau, LDraw, Teilelisten, offizielle Listen, Anleitung, Ergebnis-JSON
└── interfaces/        cli.py (Typer), api.py (FastAPI)
```

**Ablauf:** Katalog laden → Palette bauen → Bild einpassen → nach Lab umrechnen → dithern (bei `max_colors` zweimal) → Rahmen einsetzen → pro Abschnitt zerlegen → alle Writer (`result_json` zuletzt).

**Abhängigkeitsregel:**
- `domain` hängt von nichts ab.
- Fachmodule kennen nur `domain` und ihr eigenes Protocol.
- `pipeline.py` kennt nur Protocols.
- Nur `main.py` kennt konkrete Klassen.
- `interfaces/` rufen ausschließlich die Pipeline auf.
- Wo ein Fachmodul etwas aus einem anderen braucht, gibt es ein Protocol. Beispiel: `ColorSpace` in `dithering/protocol.py` wird von `imaging/color_space.py` erfüllt (Dependency Inversion).

**Patterns:**
- Composition Root: `main.py`
- Strategy: Dithering, Zerlegung, Writer
- Registry + Factory: `registry.py`
- Protocol (strukturelle Typisierung)
- Facade: `PillowImagePreparer`
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

## Entwicklung

```bash
uv run pytest                                  # alle Tests (offline)
uv run pytest --golden-aktualisieren           # Golden Files bewusst neu erzeugen
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run python scripts/ldraw_achsen_pruefen.py  # Teileausrichtung gegen LDraw prüfen
```

Die Tests in `tests/test_studio_reference.py` gleichen BrickLink-Nummern und Teileachsen mit einer lokalen Studio-Installation ab. Ohne Studio werden sie übersprungen.

## Grenzen

- Nur Draufsicht (eine Lage); ein stehendes Mosaik aus gestapelten Bricks wäre eine weitere Strategie.
- Die Greedy-Zerlegung findet nicht immer die minimale Teilezahl, ist aber schnell und deterministisch.
- Kein `.io`-Export, kein Preisabgleich, keine Prüfung der Verfügbarkeit bei Pick a Brick.
